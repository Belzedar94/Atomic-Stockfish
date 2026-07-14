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
V2_SHA256 = "4DEB05CFF79B5D5EBA51C560F64ED24224671C188B6C5DB27521033E587C87C6"
V2_VERSION = 0xA70C0002
V2_NETWORK_HASH = 0x3C1035AE
V2_TRANSFORMER_HASH = 0x5F2344B8
V2_STACK_HASH = 0x63337116
LEB128_MAGIC = b"COMPRESSED_LEB128"
V2_ACCUMULATOR_DIMENSIONS = 1024
V2_LAYER_STACKS = 8
V2_FC0_INPUTS = 1024
V2_FC0_OUTPUTS = 32
V2_FC1_INPUTS = 64
V2_FC1_OUTPUTS = 32
V2_FC2_INPUTS = 128
V2_STACK_SIZE = (
    4
    + V2_FC0_OUTPUTS * 4
    + V2_FC0_OUTPUTS * V2_FC0_INPUTS
    + V2_FC1_OUTPUTS * 4
    + V2_FC1_OUTPUTS * V2_FC1_INPUTS
    + 4
    + V2_FC2_INPUTS
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

    def evaluate(self, fen: str) -> list[str]:
        self.send(f"position fen {fen}")
        self.send("eval")
        return self.read_until(lambda line: line.startswith("Final evaluation"))

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


def require_controlled_eval(
    engine: UciProcess,
    *,
    mode: str,
    fen: str,
    expected_final: str,
    label: str,
    expected_raw: str = "+694",
) -> None:
    engine.setoption("Use NNUE", mode)
    output = engine.evaluate(fen)
    if not any(
        expected_raw in line and "side to move, internal units" in line
        for line in output
    ):
        raise AssertionError(
            f"{label} did not preserve raw V2 value {expected_raw}: {output}"
        )
    if expected_final not in output[-1] or f"[Use NNUE={mode}]" not in output[-1]:
        raise AssertionError(f"{label} final evaluation mismatch: {output[-1]}")


def evaluation_signature(output: list[str], label: str) -> tuple[str, str]:
    internal = [
        line
        for line in output
        if line.startswith("NNUE evaluation")
        and "side to move, internal units" in line
    ]
    if len(internal) != 1 or not output[-1].startswith("Final evaluation"):
        raise AssertionError(f"{label} did not produce an exact evaluation snapshot: {output}")
    return internal[0], output[-1]


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


def create_v2_weight_variant(
    source: Path, destination: Path, feature_bias_payload_offset: int
) -> None:
    """Create a valid V2B that changes both FT state and its dense consumers."""

    shutil.copyfile(source, destination)
    first_stack = _first_stack_offset(destination)
    with destination.open("r+b") as stream:
        for dimension in (2, V2_ACCUMULATOR_DIMENSIONS // 2 + 2):
            stream.seek(feature_bias_payload_offset + dimension)
            if stream.read(1) != bytes((32,)):
                raise AssertionError("unexpected controlled V2 feature bias")
            stream.seek(feature_bias_payload_offset + dimension)
            stream.write(bytes((63,)))

        connections = (
            (0, 2, 5),
            (0, V2_FC0_INPUTS // 2 + 2, 7),
            (1, 2, 11),
            (1, V2_FC0_INPUTS // 2 + 2, 13),
        )
        for stack in range(V2_LAYER_STACKS):
            fc0_weights = (
                first_stack
                + stack * V2_STACK_SIZE
                + 4
                + V2_FC0_OUTPUTS * 4
            )
            for output, input_index, expected in connections:
                offset = fc0_weights + output * V2_FC0_INPUTS + input_index
                stream.seek(offset)
                if stream.read(1) != bytes((expected,)):
                    raise AssertionError("unexpected controlled V2 fc0 weight")
                stream.seek(offset)
                stream.write(bytes((127,)))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--legacy-net", type=Path, required=True)
    parser.add_argument("--v2-net", type=Path, required=True)
    parser.add_argument(
        "--legacy-sha256",
        default=LEGACY_SHA256,
        help="expected Legacy V1 SHA-256 (defaults to the frozen strongest net)",
    )
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
    expected_legacy_sha256 = args.legacy_sha256.upper()
    if len(expected_legacy_sha256) != 64 or any(
        character not in "0123456789ABCDEF" for character in expected_legacy_sha256
    ):
        parser.error("--legacy-sha256 must be exactly 64 hexadecimal characters")
    if sha256(legacy_net) != expected_legacy_sha256:
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
            if sha256(legacy_export) != expected_legacy_sha256:
                raise AssertionError("Legacy export changed bytes")

            invalid_v2_before_switch = root / "invalid-v2-before-switch.nnue"
            mutate_u32(v2_net, invalid_v2_before_switch, 0)
            engine.setoption("EvalFile", str(invalid_v2_before_switch))
            require_rejection(engine.search(), "invalid V2 while Legacy is active")
            legacy_rollback = root / "legacy-rollback-export.nnue"
            if "Network saved successfully" not in engine.export(legacy_rollback)[-1]:
                raise AssertionError(
                    "active Legacy network could not export after rejected V2"
                )
            if sha256(legacy_rollback) != expected_legacy_sha256:
                raise AssertionError("rejected V2 load mutated the active Legacy backend")
            engine.setoption("EvalFile", str(legacy_net))
            require_search(
                engine.search(),
                "Legacy recovery after rejected V2",
                "Legacy Atomic V1",
            )

            engine.setoption("EvalFile", str(v2_net))
            for mode in ("true", "pure"):
                engine.setoption("Use NNUE", mode)
                require_search(engine.search(), f"V2 {mode}", "AtomicNNUEV2")

            start = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - {rule50} 1"
            require_controlled_eval(
                engine,
                mode="true",
                fen=start.format(rule50=0),
                expected_final="+3.34",
                label="V2 true undamped",
            )
            require_controlled_eval(
                engine,
                mode="true",
                fen=start.format(rule50=50),
                expected_final="+1.67",
                label="V2 true rule50 damping",
            )
            require_controlled_eval(
                engine,
                mode="pure",
                fen=start.format(rule50=50),
                expected_final="+3.34",
                label="V2 pure bypasses rule50",
            )

            cornered_bishop = "7k/8/8/8/8/1P6/1P6/B6K w - - 0 1"
            engine.setoption("UCI_Chess960", "true")
            require_controlled_eval(
                engine,
                mode="true",
                fen=cornered_bishop,
                expected_final="+2.27",
                label="V2 true Atomic960 correction",
                expected_raw="+672",
            )
            require_controlled_eval(
                engine,
                mode="pure",
                fen=cornered_bishop,
                expected_final="+3.23",
                label="V2 pure bypasses Atomic960 correction",
                expected_raw="+672",
            )
            engine.setoption("UCI_Chess960", "false")

            v2_export = root / "v2-export.nnue"
            if "Network saved successfully" not in engine.export(v2_export)[-1]:
                raise AssertionError("V2 export failed")
            if sha256(v2_export) != V2_SHA256:
                raise AssertionError("V2 export changed bytes")

            reloadable = root / "same-path-reload.nnue"
            weighted_variant = root / "weighted-v2b.nnue"
            shutil.copyfile(v2_net, reloadable)
            create_v2_weight_variant(v2_net, weighted_variant, leb_payload_offset)
            engine.setoption("EvalFile", str(reloadable))
            engine.setoption("Use NNUE", "true")
            require_search(engine.search(), "same-path reload baseline", "AtomicNNUEV2")
            baseline_snapshot = evaluation_signature(
                engine.evaluate(start.format(rule50=0)), "same-path V2A baseline"
            )
            with UciProcess(engine_path, args.timeout) as fresh:
                fresh.send("uci")
                fresh.read_until(lambda line: line == "uciok")
                fresh.setoption("Threads", "4")
                fresh.setoption("Hash", "16")
                fresh.setoption("EvalFile", str(weighted_variant))
                fresh.setoption("Use NNUE", "true")
                expected_variant_snapshot = evaluation_signature(
                    fresh.evaluate(start.format(rule50=0)), "fresh V2B"
                )
            if expected_variant_snapshot == baseline_snapshot:
                raise AssertionError("V2B weight fixture did not change evaluation")

            shutil.copyfile(weighted_variant, reloadable)
            reloaded_sha = sha256(reloadable)
            if reloaded_sha == V2_SHA256:
                raise AssertionError("same-path V2B fixture did not change")
            engine.setoption("EvalFile", str(reloadable))
            require_search(engine.search(), "same-path V2A-to-V2B reload", "AtomicNNUEV2")
            hot_variant_snapshot = evaluation_signature(
                engine.evaluate(start.format(rule50=0)), "hot-reloaded V2B"
            )
            if hot_variant_snapshot != expected_variant_snapshot:
                raise AssertionError(
                    "same-path V2A-to-V2B publication did not match a fresh load: "
                    f"fresh={expected_variant_snapshot}, hot={hot_variant_snapshot}"
                )
            reload_export = root / "same-path-reload-export.nnue"
            if "Network saved successfully" not in engine.export(reload_export)[-1]:
                raise AssertionError("same-path reloaded V2 network could not export")
            if sha256(reload_export) != reloaded_sha:
                raise AssertionError("same-path EvalFile update kept stale network content")
            engine.setoption("EvalFile", str(v2_net))
            require_search(engine.search(), "restore canonical V2", "AtomicNNUEV2")

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

            truncated_legacy = root / "truncated-legacy.nnue"
            with legacy_net.open("rb") as source, truncated_legacy.open("xb") as destination:
                destination.write(source.read(legacy_net.stat().st_size // 2))
            engine.setoption("EvalFile", str(truncated_legacy))
            require_rejection(
                engine.search(), "truncated Legacy payload while V2 is active"
            )
            v2_rollback = root / "v2-rollback-after-legacy-reject.nnue"
            if "Network saved successfully" not in engine.export(v2_rollback)[-1]:
                raise AssertionError("active V2 network could not export after rejected Legacy")
            if sha256(v2_rollback) != V2_SHA256:
                raise AssertionError("rejected Legacy load mutated the active V2 backend")

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
        "transactional rollback, same-path V2A->V2B publication, Threads=4, and "
        "V1->V2->V1->V2 switching"
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
