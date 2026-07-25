from __future__ import annotations

import queue
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.atomic_mining.uci_session import (
    ClockedPlayResult,
    UciEngine,
    UciOptionError,
    UciOptionSetting,
    UciProcessError,
    UciProtocolError,
    UciTimeoutError,
    parse_uci_info,
)


ROOT_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
AFTER_E4 = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"


class _BlockingReader:
    def __init__(self) -> None:
        self.lines: queue.Queue[str | None] = queue.Queue()

    def emit(self, line: str) -> None:
        self.lines.put(line + "\n")

    def close_stream(self) -> None:
        self.lines.put(None)

    def readline(self) -> str:
        line = self.lines.get()
        return "" if line is None else line


class _CommandWriter:
    def __init__(self, process: "FakeUciProcess") -> None:
        self.process = process
        self.buffer = ""
        self.closed = False

    def write(self, text: str) -> int:
        if self.closed:
            raise ValueError("closed")
        self.buffer += text
        while "\n" in self.buffer:
            command, self.buffer = self.buffer.split("\n", 1)
            self.process.handle(command)
        return len(text)

    def flush(self) -> None:
        if self.closed:
            raise ValueError("closed")

    def close(self) -> None:
        self.closed = True


class FakeUciProcess:
    """A transcript-driven process double with genuinely concurrent pipes."""

    def __init__(
        self,
        *,
        malformed_option: bool = False,
        malformed_perft: bool = False,
        perft_control_character: bool = False,
        malformed_search: str | None = None,
        silent_search: bool = False,
        die_on_uci: bool = False,
        clocked_bestmoves: tuple[str, ...] = ("e2e4", "d2d4"),
    ) -> None:
        self.stdout = _BlockingReader()
        self.stderr = _BlockingReader()
        self.stdin = _CommandWriter(self)
        self.returncode: int | None = None
        self.commands: list[str] = []
        self.malformed_option = malformed_option
        self.malformed_perft = malformed_perft
        self.perft_control_character = perft_control_character
        self.malformed_search = malformed_search
        self.silent_search = silent_search
        self.die_on_uci = die_on_uci
        self.clocked_bestmoves = list(clocked_bestmoves)
        self._done = threading.Event()
        self._last_position = ""

    def handle(self, command: str) -> None:
        self.commands.append(command)
        if command == "uci":
            if self.die_on_uci:
                self.returncode = 17
                self.stderr.emit("synthetic startup failure")
                self.stdout.close_stream()
                self.stderr.close_stream()
                self._done.set()
                return
            self.stdout.emit("id name Fake Atomic")
            if self.malformed_option:
                self.stdout.emit("option name Hash type spin min nope max 4096")
            else:
                declarations = (
                    "option name Threads type spin default 1 min 1 max 128",
                    "option name Hash type spin default 16 min 1 max 4096",
                    "option name Clear Hash type button",
                    "option name EvalFile type string default <empty>",
                    "option name MultiPV type spin default 1 min 1 max 8",
                    "option name Ponder type check default false",
                    "option name Style type combo default Normal "
                    "var Normal var Aggressive",
                )
                for declaration in declarations:
                    self.stdout.emit(declaration)
            self.stdout.emit("uciok")
            return
        if command == "isready":
            self.stdout.emit("readyok")
            return
        if command == "ucinewgame":
            return
        if command.startswith("position fen "):
            self._last_position = command
            return
        if command == "d":
            self.stdout.emit(" +---+---+")
            self.stdout.emit(f"Fen: {AFTER_E4}")
            self.stdout.emit("Key: 0123456789ABCDEF")
            self.stdout.emit("Checkers: a1 h8")
            return
        if command == "go perft 1":
            self.stdout.emit("d2d4: 1")
            self.stdout.emit(
                "e2\x01e4: 1" if self.perft_control_character else "e2e4: 1"
            )
            self.stdout.emit(
                "Nodes searched: 3" if self.malformed_perft else "Nodes searched: 2"
            )
            return
        if command.startswith("go nodes "):
            if self.silent_search:
                return
            if self.malformed_search == "info":
                self.stdout.emit("info depth not-an-integer")
                self.stdout.emit("bestmove e2e4")
                return
            if self.malformed_search == "bestmove":
                self.stdout.emit(
                    "info depth 1 score cp 0 nodes 1 pv e2e4"
                )
                self.stdout.emit("bestmove e2e4 unexpected e7e5")
                self.stdout.emit("bestmove d2d4")
                return
            # Interleave enough stderr to prove the dedicated drain is live.
            for index in range(200):
                self.stderr.emit(f"diagnostic-{index}")
            self.stdout.emit(
                "info depth 17 seldepth 25 multipv 2 score mate -3 upperbound "
                "wdl 12 34 954 nodes 63999 pv d2d4 d7d5"
            )
            self.stdout.emit(
                "info depth 18 seldepth 28 multipv 1 score cp 73 "
                "wdl 410 500 90 nodes 64000 pv e2e4 e7e5 g1f3"
            )
            self.stdout.emit("bestmove e2e4 ponder e7e5")
            return
        if command.startswith("go wtime "):
            if self.silent_search:
                return
            if not self.clocked_bestmoves:
                self.stdout.emit("bestmove (none)")
                return
            move = self.clocked_bestmoves.pop(0)
            self.stdout.emit(
                f"info depth 7 score cp 12 nodes 99 pv {move}"
            )
            self.stdout.emit(f"bestmove {move}")
            return
        if command == "stop":
            if not self.silent_search:
                self.stdout.emit("bestmove (none)")
            return
        if command == "quit":
            self.returncode = 0
            self.stdout.close_stream()
            self.stderr.close_stream()
            self._done.set()

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        if not self._done.wait(timeout):
            raise subprocess.TimeoutExpired("fake-engine", timeout)
        assert self.returncode is not None
        return self.returncode

    def terminate(self) -> None:
        self._finish(-15)

    def kill(self) -> None:
        self._finish(-9)

    def _finish(self, returncode: int) -> None:
        if self.returncode is None:
            self.returncode = returncode
            self.stdout.close_stream()
            self.stderr.close_stream()
            self._done.set()


class ProcessFactory:
    def __init__(self, process: FakeUciProcess) -> None:
        self.process = process
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(self, command: list[str], **kwargs: object) -> FakeUciProcess:
        self.calls.append((command, kwargs))
        return self.process


def _settings() -> tuple[UciOptionSetting, ...]:
    return (
        UciOptionSetting("Threads", 1),
        UciOptionSetting("Hash", 512),
        UciOptionSetting("EvalFile", "C:/nets/atomic teacher.nnue"),
        UciOptionSetting("MultiPV", 2),
        UciOptionSetting("Ponder", True),
        UciOptionSetting("Style", "aggressive"),
    )


def test_parse_info_covers_cp_mate_wdl_bounds_and_pv() -> None:
    exact = parse_uci_info(
        "info depth 18 seldepth 28 multipv 1 score cp 73 "
        "wdl 410 500 90 nodes 64000 pv e2e4 e7e5"
    )
    assert (exact.depth, exact.seldepth, exact.nodes, exact.multipv) == (
        18,
        28,
        64000,
        1,
    )
    assert exact.score is not None
    assert (exact.score.kind, exact.score.value, exact.score.bound) == (
        "cp",
        73,
        "exact",
    )
    assert exact.wdl == (410, 500, 90)
    assert exact.pv == ("e2e4", "e7e5")

    bounded = parse_uci_info(
        "info depth 17 score mate -3 upperbound nodes 63 pv d2d4"
    )
    assert bounded.score is not None
    assert (bounded.score.kind, bounded.score.value, bounded.score.bound) == (
        "mate",
        -3,
        "upper",
    )


@pytest.mark.parametrize(
    "line",
    (
        "bestmove e2e4",
        "info depth",
        "info score cp nope",
        "info score cp 3 lowerbound upperbound",
        "info lowerbound depth 4",
        "info wdl 1 two 3",
        "info multipv 0",
    ),
)
def test_parse_info_rejects_malformed_or_contradictory_fields(line: str) -> None:
    with pytest.raises(UciProtocolError):
        parse_uci_info(line)


def test_context_handshake_inspection_and_fixed_node_probe() -> None:
    process = FakeUciProcess()
    factory = ProcessFactory(process)

    with UciEngine(
        ["C:/engine/fake atomic.exe", "--uci"],
        options=_settings(),
        process_factory=factory,
    ) as engine:
        assert set(engine.option_specs) >= {
            "Threads",
            "Hash",
            "Clear Hash",
            "EvalFile",
            "MultiPV",
            "Ponder",
            "Style",
        }
        engine.new_game(timeout=1.0)
        inspection = engine.inspect_fen(ROOT_FEN, ("e2e4",))
        assert inspection.fen == AFTER_E4
        assert inspection.key == "0123456789ABCDEF"
        assert inspection.checkers == ("a1", "h8")
        assert engine.legal_moves(ROOT_FEN) == ("d2d4", "e2e4")

        result = engine.search(
            ROOT_FEN,
            nodes=64000,
            searchmoves=("e2e4", "d2d4"),
            pre_search_options=(UciOptionSetting("Clear Hash"),),
        )
        assert result.bestmove == "e2e4"
        assert result.ponder == "e7e5"
        assert set(result.latest_by_multipv) == {1, 2}
        assert result.principal is not None
        assert result.principal.score is not None
        assert result.principal.score.value == 73

        # Give the stderr reader a scheduling opportunity before observing it.
        deadline = time.monotonic() + 1.0
        while "diagnostic-199" not in engine.stderr and time.monotonic() < deadline:
            time.sleep(0.001)
        assert "diagnostic-199" in engine.stderr

    assert process.returncode == 0
    assert factory.calls[0][0] == ["C:/engine/fake atomic.exe", "--uci"]
    assert factory.calls[0][1]["stderr"] is subprocess.PIPE
    assert process.commands[:2] == [
        "uci",
        "setoption name Threads value 1",
    ]
    assert "setoption name Style value Aggressive" in process.commands
    new_game_index = process.commands.index("ucinewgame")
    assert process.commands[new_game_index + 1] == "isready"
    assert (
        f"position fen {ROOT_FEN} moves e2e4" in process.commands
    )
    assert "setoption name Clear Hash" in process.commands
    assert "go nodes 64000 searchmoves e2e4 d2d4" in process.commands
    assert "go perft 1" in process.commands
    assert process.commands[-1] == "quit"


@pytest.mark.parametrize(
    "setting, message",
    (
        (UciOptionSetting("Missing", 1), "did not declare"),
        (UciOptionSetting("Threads", 0), "outside"),
        (UciOptionSetting("Threads", True), "requires int"),
        (UciOptionSetting("Clear Hash", "yes"), "value=None"),
        (UciOptionSetting("Ponder", "true"), "requires bool"),
        (UciOptionSetting("Style", "Unknown"), "rejects value"),
        (UciOptionSetting("EvalFile"), "explicit value"),
    ),
)
def test_options_fail_closed_before_any_setting_is_applied(
    setting: UciOptionSetting, message: str
) -> None:
    process = FakeUciProcess()
    with pytest.raises(UciOptionError, match=message):
        with UciEngine(
            ["fake-engine"],
            options=(UciOptionSetting("Hash", 512), setting),
            process_factory=ProcessFactory(process),
        ):
            raise AssertionError("unreachable")
    assert not any(command.startswith("setoption ") for command in process.commands)
    assert process.commands[-1] == "quit"


def test_malformed_declaration_and_early_exit_are_distinct_failures() -> None:
    malformed = FakeUciProcess(malformed_option=True)
    with pytest.raises(UciProtocolError, match="malformed integer"):
        with UciEngine(
            ["fake-engine"],
            options=(),
            process_factory=ProcessFactory(malformed),
        ):
            raise AssertionError("unreachable")

    dead = FakeUciProcess(die_on_uci=True)
    with pytest.raises(UciProcessError, match="stdout closed|exited"):
        with UciEngine(
            ["fake-engine"],
            options=(),
            process_factory=ProcessFactory(dead),
        ):
            raise AssertionError("unreachable")


def test_search_timeout_is_bounded_and_poisons_unsynchronized_session() -> None:
    process = FakeUciProcess(silent_search=True)
    with UciEngine(
        ["fake-engine"],
        options=_settings(),
        command_timeout=0.02,
        shutdown_timeout=0.02,
        process_factory=ProcessFactory(process),
    ) as engine:
        started = time.monotonic()
        with pytest.raises(UciTimeoutError, match="bestmove"):
            engine.search(ROOT_FEN, nodes=1, timeout=0.01)
        assert time.monotonic() - started < 0.5
        with pytest.raises(UciProcessError, match="unsynchronized"):
            engine.inspect_fen(ROOT_FEN)


def test_direct_clocked_play_keeps_options_and_excludes_position_setup() -> None:
    process = FakeUciProcess()
    starts = iter((10_000, 30_000))
    completions = iter((12_500, 33_000))

    def line_clock(line: str) -> int:
        return next(completions) if line.startswith("bestmove") else 1

    with UciEngine(
        ["fake-engine"],
        options=_settings(),
        process_factory=ProcessFactory(process),
        clock_ns=lambda: next(starts),
        line_clock_ns=line_clock,
    ) as engine:
        engine.new_game(timeout=1.0)
        first = engine.play_clocked(
            ROOT_FEN,
            white_clock_ms=2_000,
            black_clock_ms=2_000,
            white_increment_ms=20,
            black_increment_ms=20,
        )
        second = engine.play_clocked(
            ROOT_FEN,
            moves=("e2e4",),
            white_clock_ms=2_017,
            black_clock_ms=2_000,
            white_increment_ms=20,
            black_increment_ms=20,
        )

    assert isinstance(first, ClockedPlayResult)
    assert (first.bestmove, first.elapsed_ns) == ("e2e4", 2_500)
    assert (second.bestmove, second.elapsed_ns) == ("d2d4", 3_000)
    first_position = process.commands.index(f"position fen {ROOT_FEN}")
    first_go = process.commands.index(
        "go wtime 2000 btime 2000 winc 20 binc 20"
    )
    second_position = process.commands.index(
        f"position fen {ROOT_FEN} moves e2e4"
    )
    second_go = process.commands.index(
        "go wtime 2017 btime 2000 winc 20 binc 20"
    )
    assert first_position < first_go < second_position < second_go
    assert engine.applied_options == _settings()
    assert engine.engine_ids == {"name": "Fake Atomic"}


def test_clocked_play_records_exact_equality_boundary() -> None:
    process = FakeUciProcess(clocked_bestmoves=("e2e4",))
    with UciEngine(
        ["fake-engine"],
        options=(),
        process_factory=ProcessFactory(process),
        clock_ns=lambda: 1_000,
        line_clock_ns=(
            lambda line: 3_000 if line.startswith("bestmove") else 0
        ),
    ) as engine:
        engine.new_game()
        result = engine.play_clocked(
            ROOT_FEN,
            white_clock_ms=2_000,
            black_clock_ms=2_000,
            white_increment_ms=0,
            black_increment_ms=0,
        )
    assert result.elapsed_ns == 2_000


def test_legal_move_perft_mismatch_fails_closed_and_poisons_session() -> None:
    process = FakeUciProcess(malformed_perft=True)
    with UciEngine(
        ["fake-engine"],
        options=(),
        process_factory=ProcessFactory(process),
    ) as engine:
        with pytest.raises(UciProtocolError, match="total differs"):
            engine.legal_moves(ROOT_FEN)
        with pytest.raises(UciProcessError, match="unsynchronized"):
            engine.inspect_fen(ROOT_FEN)


def test_legal_move_control_character_poisons_without_stale_reuse() -> None:
    process = FakeUciProcess(perft_control_character=True)
    with UciEngine(
        ["fake-engine"],
        options=(),
        process_factory=ProcessFactory(process),
    ) as engine:
        with pytest.raises(ValueError, match="control character"):
            engine.legal_moves(ROOT_FEN)
        commands_after_failure = tuple(process.commands)
        with pytest.raises(UciProcessError, match="unsynchronized"):
            engine.search(ROOT_FEN, nodes=1)
        assert tuple(process.commands) == commands_after_failure
        assert not any(command.startswith("go nodes ") for command in process.commands)


@pytest.mark.parametrize("malformed_search", ("info", "bestmove"))
def test_search_parse_failure_poisons_without_stale_bestmove_reuse(
    malformed_search: str,
) -> None:
    process = FakeUciProcess(malformed_search=malformed_search)
    with UciEngine(
        ["fake-engine"],
        options=(),
        process_factory=ProcessFactory(process),
    ) as engine:
        with pytest.raises(
            (UciProtocolError, ValueError),
            match="malformed|integer",
        ):
            engine.search(ROOT_FEN, nodes=1)
        commands_after_failure = tuple(process.commands)
        with pytest.raises(UciProcessError, match="unsynchronized"):
            engine.inspect_fen(ROOT_FEN)
        assert tuple(process.commands) == commands_after_failure
        assert process.commands.count(f"position fen {ROOT_FEN}") == 1


def test_constructor_rejects_ambiguous_commands_and_control_injection() -> None:
    with pytest.raises(TypeError, match="sequence"):
        UciEngine("fake-engine", options=())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="control"):
        UciEngine(["fake-engine", "bad\nargument"], options=())
    with pytest.raises(ValueError, match="six fields"):
        process = FakeUciProcess()
        with UciEngine(
            ["fake-engine"],
            options=(),
            process_factory=ProcessFactory(process),
        ) as engine:
            engine.inspect_fen("not a six field fen")
