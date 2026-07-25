"""Small, strict UCI subprocess session used by the Atomic mining tools.

The wrapper intentionally implements only the UCI surface needed by the
extractor and the disagreement probes.  In particular, searches are
fixed-node searches.  Engine options (including Threads, Hash, EvalFile,
MultiPV and tablebase policy) are supplied explicitly by the caller and are
validated against the engine's declarations before any of them are applied.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import queue
import re
import subprocess
import threading
import time
from types import MappingProxyType
from typing import Any, Callable, Literal, Mapping, Sequence


OptionValue = str | int | bool | None
ScoreKind = Literal["cp", "mate"]
ScoreBound = Literal["exact", "lower", "upper"]


class UciError(RuntimeError):
    """Base class for a failed or untrustworthy UCI interaction."""


class UciTimeoutError(UciError):
    """The engine did not complete a protocol operation before its deadline."""


class UciProtocolError(UciError):
    """The engine emitted malformed or contradictory UCI output."""


class UciProcessError(UciError):
    """The engine failed to start, exited early, or lost a pipe."""


class UciOptionError(UciError):
    """A requested option is absent or incompatible with its declaration."""


@dataclass(frozen=True)
class UciOptionSetting:
    """One explicit ``setoption`` request.

    ``None`` is valid only for a UCI ``button`` option.
    """

    name: str
    value: OptionValue = None


@dataclass(frozen=True)
class UciOptionSpec:
    """A validated option declaration received during the UCI handshake."""

    name: str
    kind: Literal["button", "check", "spin", "combo", "string"]
    default: str | None = None
    minimum: int | None = None
    maximum: int | None = None
    choices: tuple[str, ...] = ()
    raw: str = ""


@dataclass(frozen=True)
class UciScore:
    kind: ScoreKind
    value: int
    bound: ScoreBound = "exact"


@dataclass(frozen=True)
class UciInfo:
    depth: int | None
    seldepth: int | None
    nodes: int | None
    multipv: int | None
    score: UciScore | None
    wdl: tuple[int, int, int] | None
    pv: tuple[str, ...]
    raw: str


@dataclass(frozen=True)
class FenInspection:
    fen: str
    key: str | None
    checkers: tuple[str, ...]
    raw_lines: tuple[str, ...]


@dataclass(frozen=True)
class ProbeResult:
    bestmove: str | None
    ponder: str | None
    infos: tuple[UciInfo, ...]
    raw_lines: tuple[str, ...]

    @property
    def latest_by_multipv(self) -> dict[int, UciInfo]:
        """Return the last info record for every MultiPV lane.

        Engines normally omit ``multipv`` when it is one.  Such records belong
        to lane 1.
        """

        latest: dict[int, UciInfo] = {}
        for info in self.infos:
            latest[info.multipv or 1] = info
        return latest

    @property
    def principal(self) -> UciInfo | None:
        return self.latest_by_multipv.get(1)


@dataclass(frozen=True)
class ClockedPlayResult:
    """One direct-UCI move with result-bearing wire timestamps."""

    bestmove: str | None
    ponder: str | None
    infos: tuple[UciInfo, ...]
    raw_lines: tuple[str, ...]
    go_started_ns: int
    bestmove_completed_ns: int

    def __post_init__(self) -> None:
        for label, value in (
            ("go_started_ns", self.go_started_ns),
            ("bestmove_completed_ns", self.bestmove_completed_ns),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise UciProtocolError(f"{label} must be a non-negative integer")
        if self.bestmove_completed_ns < self.go_started_ns:
            raise UciProtocolError("bestmove completion precedes the go command")

    @property
    def elapsed_ns(self) -> int:
        return self.bestmove_completed_ns - self.go_started_ns


@dataclass(frozen=True)
class _TimestampedLine:
    text: str
    completed_ns: int


# A descriptive alias for callers that do not use the mining terminology.
SearchResult = ProbeResult


_OPTION_DECLARATION = re.compile(
    r"^option name (?P<name>.+?) type "
    r"(?P<kind>button|check|spin|combo|string)(?P<tail>(?: .*)?)$"
)
_OPTION_MARKERS = frozenset({"default", "min", "max", "var"})
_INTEGER_INFO_FIELDS = frozenset({"depth", "seldepth", "nodes", "multipv"})
_PERFT_MOVE = re.compile(r"^(?P<move>\S+):\s+(?P<nodes>\d+)$")
_PERFT_TOTAL = re.compile(r"^Nodes searched:\s+(?P<nodes>\d+)$")
_SINGLE_VALUE_INFO_FIELDS = frozenset(
    {
        "time",
        "nps",
        "hashfull",
        "tbhits",
        "sbhits",
        "cpuload",
        "currmove",
        "currmovenumber",
        "refutation",
        "currline",
    }
)


def _require_clean_text(value: str, *, label: str, allow_spaces: bool) -> str:
    if not value:
        raise ValueError(f"{label} must not be empty")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{label} contains a forbidden control character")
    if not allow_spaces and any(character.isspace() for character in value):
        raise ValueError(f"{label} must be one token")
    return value


def _parse_int(token: str, *, field: str, line: str) -> int:
    try:
        return int(token)
    except ValueError as error:
        raise UciProtocolError(
            f"malformed integer for {field!r} in UCI line: {line!r}"
        ) from error


def parse_uci_info(line: str) -> UciInfo:
    """Parse the mining-relevant fields from one complete UCI ``info`` line."""

    if not line.startswith("info"):
        raise UciProtocolError(f"expected UCI info line, got: {line!r}")
    if line != "info" and not line.startswith("info "):
        raise UciProtocolError(f"malformed UCI info prefix: {line!r}")

    tokens = line.split()[1:]
    values: dict[str, int | None] = {
        "depth": None,
        "seldepth": None,
        "nodes": None,
        "multipv": None,
    }
    score_kind: ScoreKind | None = None
    score_value: int | None = None
    lowerbound = False
    upperbound = False
    wdl: tuple[int, int, int] | None = None
    pv: tuple[str, ...] = ()

    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "string":
            break
        if token in _INTEGER_INFO_FIELDS:
            if index + 1 >= len(tokens):
                raise UciProtocolError(
                    f"missing value for {token!r} in UCI line: {line!r}"
                )
            if values[token] is not None:
                raise UciProtocolError(
                    f"duplicate {token!r} in UCI line: {line!r}"
                )
            parsed = _parse_int(tokens[index + 1], field=token, line=line)
            if parsed < 0 or (token == "multipv" and parsed == 0):
                raise UciProtocolError(
                    f"out-of-range {token!r} in UCI line: {line!r}"
                )
            values[token] = parsed
            index += 2
            continue
        if token == "score":
            if score_kind is not None or score_value is not None:
                raise UciProtocolError(f"duplicate score in UCI line: {line!r}")
            if index + 2 >= len(tokens) or tokens[index + 1] not in {"cp", "mate"}:
                raise UciProtocolError(f"malformed score in UCI line: {line!r}")
            score_kind = tokens[index + 1]  # type: ignore[assignment]
            score_value = _parse_int(tokens[index + 2], field="score", line=line)
            index += 3
            continue
        if token == "lowerbound":
            if lowerbound:
                raise UciProtocolError(
                    f"duplicate lowerbound in UCI line: {line!r}"
                )
            lowerbound = True
            index += 1
            continue
        if token == "upperbound":
            if upperbound:
                raise UciProtocolError(
                    f"duplicate upperbound in UCI line: {line!r}"
                )
            upperbound = True
            index += 1
            continue
        if token == "wdl":
            if wdl is not None or index + 3 >= len(tokens):
                raise UciProtocolError(f"malformed WDL in UCI line: {line!r}")
            wdl_values = tuple(
                _parse_int(candidate, field="wdl", line=line)
                for candidate in tokens[index + 1 : index + 4]
            )
            if any(value < 0 for value in wdl_values):
                raise UciProtocolError(
                    f"negative WDL component in UCI line: {line!r}"
                )
            wdl = (wdl_values[0], wdl_values[1], wdl_values[2])
            index += 4
            continue
        if token == "pv":
            pv = tuple(tokens[index + 1 :])
            break
        if token in _SINGLE_VALUE_INFO_FIELDS:
            # These standard fields are not currently needed by the mining
            # scorer.  Consume their first value so a numeric value is never
            # mistaken for a keyword.  ``refutation``/``currline`` may contain
            # more moves; unknown tokens are intentionally ignored below.
            index += 2
            continue
        index += 1

    if lowerbound and upperbound:
        raise UciProtocolError(
            f"score cannot be both lowerbound and upperbound: {line!r}"
        )
    if (lowerbound or upperbound) and score_kind is None:
        raise UciProtocolError(f"score bound without score: {line!r}")
    score = None
    if score_kind is not None:
        assert score_value is not None
        bound: ScoreBound = (
            "lower" if lowerbound else "upper" if upperbound else "exact"
        )
        score = UciScore(kind=score_kind, value=score_value, bound=bound)

    return UciInfo(
        depth=values["depth"],
        seldepth=values["seldepth"],
        nodes=values["nodes"],
        multipv=values["multipv"],
        score=score,
        wdl=wdl,
        pv=pv,
        raw=line,
    )


def _parse_option_declaration(line: str) -> UciOptionSpec:
    match = _OPTION_DECLARATION.fullmatch(line)
    if match is None:
        raise UciProtocolError(f"malformed UCI option declaration: {line!r}")
    name = _require_clean_text(
        match.group("name").strip(), label="UCI option name", allow_spaces=True
    )
    kind = match.group("kind")
    tail_tokens = match.group("tail").strip().split()
    fields: dict[str, list[str]] = {}
    index = 0
    while index < len(tail_tokens):
        marker = tail_tokens[index]
        if marker not in _OPTION_MARKERS:
            raise UciProtocolError(
                f"malformed metadata in UCI option declaration: {line!r}"
            )
        index += 1
        start = index
        while index < len(tail_tokens) and tail_tokens[index] not in _OPTION_MARKERS:
            index += 1
        value = " ".join(tail_tokens[start:index])
        fields.setdefault(marker, []).append(value)

    default_values = fields.get("default", [])
    if len(default_values) > 1:
        raise UciProtocolError(f"duplicate option default: {line!r}")
    default = default_values[0] if default_values else None
    minimum = None
    maximum = None
    if kind == "spin":
        minimum_values = fields.get("min", [])
        maximum_values = fields.get("max", [])
        if len(minimum_values) != 1 or len(maximum_values) != 1:
            raise UciProtocolError(
                f"spin option needs exactly one min and max: {line!r}"
            )
        minimum = _parse_int(minimum_values[0], field="option min", line=line)
        maximum = _parse_int(maximum_values[0], field="option max", line=line)
        if minimum > maximum:
            raise UciProtocolError(f"option min exceeds max: {line!r}")
    elif "min" in fields or "max" in fields:
        raise UciProtocolError(f"non-spin option declares min/max: {line!r}")

    choices = tuple(fields.get("var", ()))
    if kind == "combo" and not choices:
        raise UciProtocolError(f"combo option has no choices: {line!r}")
    if kind != "combo" and choices:
        raise UciProtocolError(f"non-combo option declares choices: {line!r}")

    return UciOptionSpec(
        name=name,
        kind=kind,  # type: ignore[arg-type]
        default=default,
        minimum=minimum,
        maximum=maximum,
        choices=choices,
        raw=line,
    )


class UciEngine:
    """A single-use, context-managed UCI subprocess.

    The object may not be restarted after it has been closed.  This avoids
    accidentally comparing engines with different hidden process state.
    """

    def __init__(
        self,
        command: Sequence[str | os.PathLike[str]],
        *,
        options: Sequence[UciOptionSetting],
        startup_timeout: float = 10.0,
        command_timeout: float = 120.0,
        shutdown_timeout: float = 2.0,
        cwd: str | os.PathLike[str] | None = None,
        env: Mapping[str, str] | None = None,
        process_factory: Callable[..., Any] | None = None,
        clock_ns: Callable[[], int] | None = None,
        line_clock_ns: Callable[[str], int] | None = None,
        expected_handshake_preamble: Sequence[str] = (),
        expected_identity_order: Sequence[str] = (),
        expect_single_blank_after_ids: bool = False,
    ) -> None:
        if isinstance(command, (str, bytes, os.PathLike)):
            raise TypeError("command must be a sequence of arguments, not one path")
        normalized_command = tuple(os.fspath(argument) for argument in command)
        if not normalized_command:
            raise ValueError("command must not be empty")
        for index, argument in enumerate(normalized_command):
            _require_clean_text(
                argument, label=f"command argument {index}", allow_spaces=True
            )
        if not isinstance(options, Sequence) or isinstance(options, (str, bytes)):
            raise TypeError("options must be a sequence of UciOptionSetting")
        normalized_options = tuple(options)
        if any(not isinstance(setting, UciOptionSetting) for setting in normalized_options):
            raise TypeError("options must contain only UciOptionSetting values")
        if not isinstance(expected_handshake_preamble, Sequence) or isinstance(
            expected_handshake_preamble, (str, bytes)
        ):
            raise TypeError("expected_handshake_preamble must be a sequence of lines")
        normalized_preamble = tuple(expected_handshake_preamble)
        for index, line in enumerate(normalized_preamble):
            _require_clean_text(
                line,
                label=f"expected handshake preamble line {index}",
                allow_spaces=True,
            )
            if line == "uciok" or line.startswith(("id ", "option ")):
                raise ValueError(
                    "expected handshake preamble must not contain UCI declarations"
                )
        if not isinstance(expected_identity_order, Sequence) or isinstance(
            expected_identity_order, (str, bytes)
        ):
            raise TypeError("expected_identity_order must be a sequence")
        normalized_identity_order = tuple(expected_identity_order)
        if (
            any(
                not isinstance(identity, str)
                or identity not in {"name", "author"}
                for identity in normalized_identity_order
            )
            or len(normalized_identity_order)
            != len(set(normalized_identity_order))
        ):
            raise ValueError(
                "expected_identity_order must contain unique name/author keys"
            )
        if not isinstance(expect_single_blank_after_ids, bool):
            raise TypeError("expect_single_blank_after_ids must be bool")
        for label, value in (
            ("startup_timeout", startup_timeout),
            ("command_timeout", command_timeout),
            ("shutdown_timeout", shutdown_timeout),
        ):
            if isinstance(value, bool) or value <= 0:
                raise ValueError(f"{label} must be positive")

        self.command = normalized_command
        self.initial_options = normalized_options
        self.expected_handshake_preamble = normalized_preamble
        self.expected_identity_order = normalized_identity_order
        self.expect_single_blank_after_ids = expect_single_blank_after_ids
        self.startup_timeout = float(startup_timeout)
        self.command_timeout = float(command_timeout)
        self.shutdown_timeout = float(shutdown_timeout)
        self.cwd = Path(cwd) if cwd is not None else None
        self.env = dict(env) if env is not None else None
        self._process_factory = process_factory or subprocess.Popen
        self._clock_ns = clock_ns or time.monotonic_ns
        self._line_clock_ns = line_clock_ns or (
            lambda _line: time.monotonic_ns()
        )

        self._process: Any | None = None
        self._stdin: Any | None = None
        self._stdout_events: queue.Queue[tuple[str, object | None]] = queue.Queue()
        self._stderr_lines: list[str] = []
        self._stderr_lock = threading.Lock()
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._option_specs: dict[str, UciOptionSpec] = {}
        self._engine_ids: dict[str, str] = {}
        self._handshake_preamble: list[str] = []
        self._handshake_identity_order: list[str] = []
        self._handshake_blank_after_ids = False
        self._applied_options: list[UciOptionSetting] = []
        self._entered = False
        self._closed = False
        self._usable = False

    @property
    def option_specs(self) -> Mapping[str, UciOptionSpec]:
        return MappingProxyType(
            {spec.name: spec for spec in self._option_specs.values()}
        )

    @property
    def engine_ids(self) -> Mapping[str, str]:
        return MappingProxyType(dict(self._engine_ids))

    @property
    def handshake_preamble(self) -> tuple[str, ...]:
        return tuple(self._handshake_preamble)

    @property
    def handshake_identity_order(self) -> tuple[str, ...]:
        return tuple(self._handshake_identity_order)

    @property
    def handshake_blank_after_ids(self) -> bool:
        return self._handshake_blank_after_ids

    @property
    def applied_options(self) -> tuple[UciOptionSetting, ...]:
        return tuple(self._applied_options)

    @property
    def stderr(self) -> str:
        with self._stderr_lock:
            return "\n".join(self._stderr_lines)

    def __enter__(self) -> "UciEngine":
        if self._entered or self._closed:
            raise UciProcessError("a UciEngine instance is single-use")
        self._entered = True
        try:
            self._start_process()
            self._handshake()
            self._usable = True
            return self
        except BaseException:
            self.close()
            raise

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> bool:
        try:
            self.close()
        except BaseException:
            if exc_type is None:
                raise
        return False

    def _start_process(self) -> None:
        try:
            process = self._process_factory(
                list(self.command),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="strict",
                bufsize=1,
                cwd=str(self.cwd) if self.cwd is not None else None,
                env=self.env,
            )
        except (OSError, ValueError) as error:
            raise UciProcessError(
                f"could not start UCI engine {self.command[0]!r}: {error}"
            ) from error
        if process.stdin is None or process.stdout is None or process.stderr is None:
            try:
                process.kill()
            except BaseException:
                pass
            raise UciProcessError("UCI engine was started without all three pipes")

        self._process = process
        self._stdin = process.stdin
        self._stdout_thread = threading.Thread(
            target=self._read_stdout,
            args=(process.stdout,),
            name="atomic-mining-uci-stdout",
            daemon=True,
        )
        self._stderr_thread = threading.Thread(
            target=self._read_stderr,
            args=(process.stderr,),
            name="atomic-mining-uci-stderr",
            daemon=True,
        )
        self._stdout_thread.start()
        self._stderr_thread.start()

    def _read_stdout(self, stream: Any) -> None:
        try:
            while True:
                line = stream.readline()
                if line == "":
                    break
                rendered = line.rstrip("\r\n")
                completed_ns = self._line_clock_ns(rendered)
                if (
                    isinstance(completed_ns, bool)
                    or not isinstance(completed_ns, int)
                    or completed_ns < 0
                ):
                    raise UciProtocolError(
                        "stdout completion clock returned an invalid timestamp"
                    )
                self._stdout_events.put(
                    (
                        "line",
                        _TimestampedLine(rendered, completed_ns),
                    )
                )
        except BaseException as error:
            self._stdout_events.put(("error", error))
        finally:
            self._stdout_events.put(("eof", None))

    def _read_stderr(self, stream: Any) -> None:
        try:
            while True:
                line = stream.readline()
                if line == "":
                    break
                with self._stderr_lock:
                    self._stderr_lines.append(line.rstrip("\r\n"))
        except BaseException as error:
            with self._stderr_lock:
                self._stderr_lines.append(f"[stderr reader failed: {error!r}]")

    def _stderr_suffix(self) -> str:
        with self._stderr_lock:
            lines = self._stderr_lines[-20:]
        if not lines:
            return ""
        return "; stderr tail: " + " | ".join(lines)

    def _write(self, command: str) -> None:
        self._assert_live()
        _require_clean_text(command, label="UCI command", allow_spaces=True)
        assert self._stdin is not None
        try:
            self._stdin.write(command + "\n")
            self._stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as error:
            raise UciProcessError(
                f"could not write UCI command {command!r}{self._stderr_suffix()}"
            ) from error

    def _assert_live(self) -> None:
        if self._process is None or self._closed:
            raise UciProcessError("UCI engine is not running")
        return_code = self._process.poll()
        if return_code is not None:
            raise UciProcessError(
                f"UCI engine exited with code {return_code}{self._stderr_suffix()}"
            )

    def _assert_usable(self) -> None:
        self._assert_live()
        if not self._usable:
            raise UciProcessError("UCI session is not ready or became unsynchronized")

    def _read_timestamped_line(
        self, deadline: float, *, operation: str
    ) -> _TimestampedLine:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise UciTimeoutError(
                    f"timed out while waiting for {operation}{self._stderr_suffix()}"
                )
            try:
                event, payload = self._stdout_events.get(timeout=remaining)
            except queue.Empty as error:
                try:
                    self._assert_live()
                except UciProcessError:
                    raise
                raise UciTimeoutError(
                    f"timed out while waiting for {operation}{self._stderr_suffix()}"
                ) from error
            if event == "line":
                assert isinstance(payload, _TimestampedLine)
                return payload
            if event == "error":
                raise UciProcessError(
                    f"UCI stdout reader failed: {payload!r}{self._stderr_suffix()}"
                )
            if event == "eof":
                return_code = (
                    self._process.poll() if self._process is not None else None
                )
                raise UciProcessError(
                    f"UCI stdout closed unexpectedly (exit={return_code})"
                    f"{self._stderr_suffix()}"
                )
            raise AssertionError(f"unknown reader event: {event!r}")

    def _read_line(self, deadline: float, *, operation: str) -> str:
        return self._read_timestamped_line(
            deadline, operation=operation
        ).text

    @staticmethod
    def _deadline(timeout: float | None, default: float) -> float:
        duration = default if timeout is None else timeout
        if isinstance(duration, bool) or duration <= 0:
            raise ValueError("timeout must be positive")
        return time.monotonic() + float(duration)

    def _handshake(self) -> None:
        self._write("uci")
        deadline = self._deadline(self.startup_timeout, self.startup_timeout)
        specifications: dict[str, UciOptionSpec] = {}
        identities: dict[str, str] = {}
        while True:
            line = self._read_line(deadline, operation="uciok")
            if len(self._handshake_preamble) < len(
                self.expected_handshake_preamble
            ):
                expected = self.expected_handshake_preamble[
                    len(self._handshake_preamble)
                ]
                if line != expected:
                    raise UciProtocolError(
                        "engine omitted or changed the expected UCI handshake "
                        f"preamble at line {len(self._handshake_preamble)}"
                    )
                self._handshake_preamble.append(line)
                continue
            if line == "":
                if (
                    self.expect_single_blank_after_ids
                    and not self._handshake_blank_after_ids
                    and set(identities) == {"name", "author"}
                    and (
                        not self.expected_identity_order
                        or tuple(self._handshake_identity_order)
                        == self.expected_identity_order
                    )
                    and not specifications
                ):
                    self._handshake_blank_after_ids = True
                    continue
                raise UciProtocolError(
                    "unexpected blank line during UCI handshake"
                )
            if line == "uciok":
                if (
                    self.expect_single_blank_after_ids
                    and not self._handshake_blank_after_ids
                ):
                    raise UciProtocolError(
                        "engine omitted the expected blank UCI separator"
                    )
                break
            if line.startswith("option "):
                if (
                    self.expect_single_blank_after_ids
                    and not self._handshake_blank_after_ids
                ):
                    raise UciProtocolError(
                        "engine omitted the expected blank UCI separator"
                    )
                specification = _parse_option_declaration(line)
                key = specification.name.casefold()
                if key in specifications:
                    raise UciProtocolError(
                        f"duplicate UCI option name: {specification.name!r}"
                    )
                specifications[key] = specification
                continue
            if line.startswith("id "):
                if self._handshake_blank_after_ids:
                    raise UciProtocolError(
                        "UCI identity appeared after the blank separator"
                    )
                tokens = line.split(maxsplit=2)
                if (
                    len(tokens) != 3
                    or tokens[1] not in {"name", "author"}
                    or not tokens[2]
                    or tokens[1] in identities
                ):
                    raise UciProtocolError(
                        f"malformed or duplicate UCI identity: {line!r}"
                    )
                if (
                    self.expected_identity_order
                    and (
                        len(self._handshake_identity_order)
                        >= len(self.expected_identity_order)
                        or tokens[1]
                        != self.expected_identity_order[
                            len(self._handshake_identity_order)
                        ]
                    )
                ):
                    raise UciProtocolError(
                        "UCI identity order differs from the expected "
                        "handshake contract"
                    )
                _require_clean_text(
                    tokens[2],
                    label=f"UCI id {tokens[1]}",
                    allow_spaces=True,
                )
                identities[tokens[1]] = tokens[2]
                self._handshake_identity_order.append(tokens[1])
                continue
            raise UciProtocolError(
                f"unexpected line during UCI handshake: {line!r}"
            )
        self._option_specs = specifications
        self._engine_ids = identities
        if (
            self.expected_identity_order
            and tuple(self._handshake_identity_order)
            != self.expected_identity_order
        ):
            raise UciProtocolError(
                "engine omitted an expected UCI identity declaration"
            )

        # Validate the complete list before mutating engine state.
        rendered = tuple(
            self._validate_option(setting) for setting in self.initial_options
        )
        for command in rendered:
            self._write(command)
        self._applied_options.extend(self.initial_options)
        self._synchronize(deadline=self._deadline(None, self.startup_timeout))

    def _validate_option(self, setting: UciOptionSetting) -> str:
        name = _require_clean_text(
            setting.name, label="UCI option setting name", allow_spaces=True
        )
        specification = self._option_specs.get(name.casefold())
        if specification is None:
            raise UciOptionError(f"engine did not declare option {name!r}")
        value = setting.value

        if specification.kind == "button":
            if value is not None:
                raise UciOptionError(
                    f"button option {specification.name!r} requires value=None"
                )
            return f"setoption name {specification.name}"
        if value is None:
            raise UciOptionError(
                f"option {specification.name!r} requires an explicit value"
            )
        if specification.kind == "check":
            if not isinstance(value, bool):
                raise UciOptionError(
                    f"check option {specification.name!r} requires bool"
                )
            rendered_value = "true" if value else "false"
        elif specification.kind == "spin":
            if not isinstance(value, int) or isinstance(value, bool):
                raise UciOptionError(
                    f"spin option {specification.name!r} requires int"
                )
            assert specification.minimum is not None
            assert specification.maximum is not None
            if not specification.minimum <= value <= specification.maximum:
                raise UciOptionError(
                    f"spin option {specification.name!r} value {value} is outside "
                    f"[{specification.minimum}, {specification.maximum}]"
                )
            rendered_value = str(value)
        elif specification.kind == "combo":
            if not isinstance(value, str):
                raise UciOptionError(
                    f"combo option {specification.name!r} requires str"
                )
            matching = [
                choice
                for choice in specification.choices
                if choice.casefold() == value.casefold()
            ]
            if len(matching) != 1:
                raise UciOptionError(
                    f"combo option {specification.name!r} rejects value {value!r}"
                )
            rendered_value = matching[0]
        else:
            if not isinstance(value, str):
                raise UciOptionError(
                    f"string option {specification.name!r} requires str"
                )
            rendered_value = value

        if specification.kind == "string" and rendered_value == "":
            # The UCI wire represents an intentionally empty string as a
            # present ``value`` marker with no following token.  This is
            # required for explicit tablebase-off contracts such as
            # ``SyzygyPath=""``; omitting the option would leave ambient
            # engine state unbound.
            pass
        else:
            _require_clean_text(
                rendered_value,
                label=f"value for UCI option {specification.name!r}",
                allow_spaces=True,
            )
        return f"setoption name {specification.name} value {rendered_value}"

    def _synchronize(
        self, *, timeout: float | None = None, deadline: float | None = None
    ) -> None:
        if deadline is None:
            deadline = self._deadline(timeout, self.command_timeout)
        self._write("isready")
        while True:
            line = self._read_line(deadline, operation="readyok")
            if line == "readyok":
                return

    def set_options(
        self,
        settings: Sequence[UciOptionSetting],
        *,
        timeout: float | None = None,
    ) -> None:
        """Validate and atomically submit an explicit group of option requests."""

        self._assert_usable()
        normalized = tuple(settings)
        if any(not isinstance(setting, UciOptionSetting) for setting in normalized):
            raise TypeError("settings must contain only UciOptionSetting values")
        rendered = tuple(self._validate_option(setting) for setting in normalized)
        for command in rendered:
            self._write(command)
        self._applied_options.extend(normalized)
        self._synchronize(timeout=timeout)

    def new_game(self, *, timeout: float | None = None) -> None:
        """Reset engine game state and wait until the reset is observable."""

        self._assert_usable()
        self._write("ucinewgame")
        self._synchronize(timeout=timeout)

    @staticmethod
    def _position_command(root_fen: str, moves: Sequence[str]) -> str:
        fen = _require_clean_text(
            root_fen.strip(), label="root FEN", allow_spaces=True
        )
        if len(fen.split()) != 6:
            raise ValueError("root FEN must contain exactly six fields")
        normalized_moves = tuple(moves)
        for index, move in enumerate(normalized_moves):
            _require_clean_text(
                move, label=f"position move {index}", allow_spaces=False
            )
        suffix = " moves " + " ".join(normalized_moves) if normalized_moves else ""
        return f"position fen {fen}{suffix}"

    def inspect_fen(
        self,
        root_fen: str,
        moves: Sequence[str] = (),
        *,
        timeout: float | None = None,
    ) -> FenInspection:
        """Replay ``moves`` and return the engine's canonical ``d`` inspection."""

        self._assert_usable()
        deadline = self._deadline(timeout, self.command_timeout)
        self._write(self._position_command(root_fen, moves))
        self._write("d")
        self._write("isready")
        lines: list[str] = []
        while True:
            line = self._read_line(deadline, operation="position inspection")
            if line == "readyok":
                break
            lines.append(line)

        fen_values = [line[5:].strip() for line in lines if line.startswith("Fen: ")]
        key_values = [line[5:].strip() for line in lines if line.startswith("Key: ")]
        checker_values = [
            line[len("Checkers:") :].strip()
            for line in lines
            if line.startswith("Checkers:")
        ]
        if len(fen_values) != 1 or not fen_values[0]:
            raise UciProtocolError(
                "position inspection must contain exactly one non-empty Fen: line"
            )
        if len(key_values) > 1 or len(checker_values) > 1:
            raise UciProtocolError(
                "position inspection contains duplicate Key:/Checkers: lines"
            )
        if len(fen_values[0].split()) != 6:
            raise UciProtocolError(
                f"engine returned malformed canonical FEN: {fen_values[0]!r}"
            )
        key = key_values[0] if key_values and key_values[0] else None
        checkers = tuple(checker_values[0].split()) if checker_values else ()
        return FenInspection(
            fen=fen_values[0],
            key=key,
            checkers=checkers,
            raw_lines=tuple(lines),
        )

    def legal_moves(
        self,
        root_fen: str,
        moves: Sequence[str] = (),
        *,
        timeout: float | None = None,
    ) -> tuple[str, ...]:
        """Return the complete legal move set through a depth-one perft divide.

        Unlike ``position ... moves``, the perft divide gives the caller
        positive evidence that a proposed move is legal.  At depth one every
        divide count must be one and the reported total must equal the number
        of distinct moves; any deviation poisons the result.
        """

        self._assert_usable()
        deadline = self._deadline(timeout, self.command_timeout)
        self._write(self._position_command(root_fen, moves))
        self._write("go perft 1")
        legal: list[str] = []
        seen: set[str] = set()
        try:
            while True:
                line = self._read_line(deadline, operation="perft depth 1")
                _require_clean_text(
                    line, label="depth-one perft output", allow_spaces=True
                )
                total_match = _PERFT_TOTAL.fullmatch(line)
                if total_match is not None:
                    total = int(total_match.group("nodes"))
                    if total != len(legal):
                        raise UciProtocolError(
                            "depth-one perft total differs from legal move count"
                        )
                    return tuple(sorted(legal))
                move_match = _PERFT_MOVE.fullmatch(line)
                if move_match is None:
                    continue
                move = move_match.group("move")
                nodes = int(move_match.group("nodes"))
                if nodes != 1:
                    raise UciProtocolError(
                        f"depth-one perft move {move!r} has node count {nodes}"
                    )
                if move in seen:
                    raise UciProtocolError(
                        f"depth-one perft repeated legal move {move!r}"
                    )
                _require_clean_text(
                    move, label="depth-one perft move", allow_spaces=False
                )
                seen.add(move)
                legal.append(move)
        except Exception:
            # Perft has no bestmove delimiter with which to resynchronize after
            # malformed or missing completion output.  This includes local
            # parser failures (for example a control character in a move):
            # leaving any queued divide/total lines available to a later
            # command could authenticate stale output as a fresh result.
            self._usable = False
            raise

    def search(
        self,
        root_fen: str,
        *,
        nodes: int,
        moves: Sequence[str] = (),
        searchmoves: Sequence[str] = (),
        pre_search_options: Sequence[UciOptionSetting] = (),
        timeout: float | None = None,
    ) -> ProbeResult:
        """Run one fixed-node probe and return all structured ``info`` lanes."""

        self._assert_usable()
        if not isinstance(nodes, int) or isinstance(nodes, bool) or nodes <= 0:
            raise ValueError("nodes must be a positive integer")
        normalized_searchmoves = tuple(searchmoves)
        for index, move in enumerate(normalized_searchmoves):
            _require_clean_text(
                move, label=f"searchmove {index}", allow_spaces=False
            )
        if pre_search_options:
            self.set_options(pre_search_options, timeout=timeout)

        deadline = self._deadline(timeout, self.command_timeout)
        self._write(self._position_command(root_fen, moves))
        go = f"go nodes {nodes}"
        if normalized_searchmoves:
            go += " searchmoves " + " ".join(normalized_searchmoves)
        self._write(go)

        raw_lines: list[str] = []
        infos: list[UciInfo] = []
        try:
            while True:
                line = self._read_line(deadline, operation="bestmove")
                _require_clean_text(line, label="search output", allow_spaces=True)
                raw_lines.append(line)
                if line.startswith("info"):
                    infos.append(parse_uci_info(line))
                    continue
                if line.startswith("bestmove"):
                    bestmove, ponder = self._parse_bestmove(line)
                    return ProbeResult(
                        bestmove=bestmove,
                        ponder=ponder,
                        infos=tuple(infos),
                        raw_lines=tuple(raw_lines),
                    )
        except Exception:
            # Once ``go`` has been accepted, every parse/protocol/process
            # failure is an unrecoverable trust failure for this single-use
            # wrapper.  Poisoning prevents a queued bestmove from the failed
            # search being consumed by any subsequent operation.
            self._usable = False
            raise

    def play_clocked(
        self,
        root_fen: str,
        *,
        moves: Sequence[str] = (),
        white_clock_ms: int,
        black_clock_ms: int,
        white_increment_ms: int,
        black_increment_ms: int,
        timeout: float | None = None,
    ) -> ClockedPlayResult:
        """Send one direct clocked move with setup excluded from elapsed time.

        The position is written before the result-bearing timestamp.  Timing
        starts immediately before writing and flushing the complete ``go``
        command.  The stdout reader timestamps a line only after ``readline``
        has received its newline, so a partial ``bestmove`` cannot stop the
        clock.
        """

        self._assert_usable()
        clocks = (
            ("white_clock_ms", white_clock_ms),
            ("black_clock_ms", black_clock_ms),
            ("white_increment_ms", white_increment_ms),
            ("black_increment_ms", black_increment_ms),
        )
        for label, value in clocks:
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{label} must be a non-negative integer")
        deadline = self._deadline(timeout, self.command_timeout)
        self._write(self._position_command(root_fen, moves))
        go = (
            f"go wtime {white_clock_ms} btime {black_clock_ms} "
            f"winc {white_increment_ms} binc {black_increment_ms}"
        )
        _require_clean_text(go, label="clocked go command", allow_spaces=True)
        self._assert_live()
        assert self._stdin is not None
        go_started_ns = self._clock_ns()
        if (
            isinstance(go_started_ns, bool)
            or not isinstance(go_started_ns, int)
            or go_started_ns < 0
        ):
            raise UciProtocolError("go clock returned an invalid timestamp")
        try:
            self._stdin.write(go + "\n")
            self._stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as error:
            self._usable = False
            raise UciProcessError(
                f"could not write clocked go command{self._stderr_suffix()}"
            ) from error

        raw_lines: list[str] = []
        infos: list[UciInfo] = []
        try:
            while True:
                event = self._read_timestamped_line(
                    deadline, operation="clocked bestmove"
                )
                line = event.text
                _require_clean_text(
                    line, label="clocked search output", allow_spaces=True
                )
                raw_lines.append(line)
                if line.startswith("info"):
                    infos.append(parse_uci_info(line))
                    continue
                if line.startswith("bestmove"):
                    bestmove, ponder = self._parse_bestmove(line)
                    return ClockedPlayResult(
                        bestmove=bestmove,
                        ponder=ponder,
                        infos=tuple(infos),
                        raw_lines=tuple(raw_lines),
                        go_started_ns=go_started_ns,
                        bestmove_completed_ns=event.completed_ns,
                    )
                raise UciProtocolError(
                    f"unexpected clocked search output: {line!r}"
                )
        except Exception:
            self._usable = False
            raise

    @staticmethod
    def _parse_bestmove(line: str) -> tuple[str | None, str | None]:
        _require_clean_text(line, label="bestmove output", allow_spaces=True)
        tokens = line.split()
        if len(tokens) not in {2, 4} or tokens[0] != "bestmove":
            raise UciProtocolError(f"malformed bestmove line: {line!r}")
        if len(tokens) == 4 and tokens[2] != "ponder":
            raise UciProtocolError(f"malformed bestmove ponder: {line!r}")
        bestmove = None if tokens[1] in {"(none)", "0000"} else tokens[1]
        if bestmove is not None:
            _require_clean_text(
                bestmove, label="bestmove move", allow_spaces=False
            )
        ponder = None
        if len(tokens) == 4:
            ponder = None if tokens[3] in {"(none)", "0000"} else tokens[3]
            if ponder is not None:
                _require_clean_text(
                    ponder, label="bestmove ponder", allow_spaces=False
                )
        return bestmove, ponder

    def close(self) -> None:
        if self._closed:
            return
        self._usable = False
        process = self._process
        failures: list[str] = []
        if process is not None and process.poll() is None:
            try:
                if self._stdin is not None:
                    self._stdin.write("quit\n")
                    self._stdin.flush()
            except (BrokenPipeError, OSError, ValueError) as error:
                failures.append(f"quit write failed: {error!r}")
            try:
                process.wait(timeout=self.shutdown_timeout)
            except (subprocess.TimeoutExpired, TimeoutError):
                try:
                    process.terminate()
                except BaseException as error:
                    failures.append(f"terminate failed: {error!r}")
                try:
                    process.wait(timeout=self.shutdown_timeout)
                except (subprocess.TimeoutExpired, TimeoutError):
                    try:
                        process.kill()
                    except BaseException as error:
                        failures.append(f"kill failed: {error!r}")
                    try:
                        process.wait(timeout=self.shutdown_timeout)
                    except BaseException as error:
                        failures.append(f"post-kill wait failed: {error!r}")
                except BaseException as error:
                    failures.append(f"post-terminate wait failed: {error!r}")
            except BaseException as error:
                failures.append(f"graceful wait failed: {error!r}")
        if process is not None:
            try:
                if process.poll() is None:
                    failures.append("engine process remained active")
            except BaseException as error:
                failures.append(f"terminal poll failed: {error!r}")
        if self._stdin is not None:
            try:
                self._stdin.close()
            except (OSError, ValueError) as error:
                failures.append(f"stdin close failed: {error!r}")
        for thread in (self._stdout_thread, self._stderr_thread):
            if thread is not None:
                thread.join(timeout=self.shutdown_timeout)
                if thread.is_alive():
                    failures.append(f"{thread.name} did not terminate")
        if process is not None:
            for label in ("stdout", "stderr"):
                stream = getattr(process, label, None)
                close = getattr(stream, "close", None)
                if callable(close):
                    try:
                        close()
                    except (OSError, ValueError) as error:
                        failures.append(f"{label} close failed: {error!r}")
        self._closed = True
        if failures:
            raise UciProcessError(
                "UCI shutdown was not proven: " + "; ".join(failures)
            )


__all__ = [
    "ClockedPlayResult",
    "FenInspection",
    "ProbeResult",
    "SearchResult",
    "UciEngine",
    "UciError",
    "UciInfo",
    "UciOptionError",
    "UciOptionSetting",
    "UciOptionSpec",
    "UciProcessError",
    "UciProtocolError",
    "UciScore",
    "UciTimeoutError",
    "parse_uci_info",
]
