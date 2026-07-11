#!/usr/bin/env python3
"""Cooperative, pair-safe, immutable LOS gate for variantfishtest_new1.py."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import importlib.util
from pathlib import Path
import platform
import psutil
import re
import subprocess
import sys
import threading
import time
from types import ModuleType
from typing import Callable, Mapping, Optional, Sequence, Tuple

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from atomic_compiler_preflight import (
    CompilationSignature,
    CompilerPreflightError,
    FileFingerprint,
    NORMATIVE_BASELINE_SHA256,
    fingerprint_file,
    fingerprint_files,
    require_matching_compilation_settings,
    require_sha256,
    verify_file_fingerprints,
)


LOS_RE = re.compile(r"(?:^|\s)LOS:\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))%")

EXPECTED_RUNNER_SHA256 = (
    "37D1790096520D9F3A1003746CDFBED59D2CC125A9B3D3192FF3399295EC9D70"
)
EXPECTED_STAT_UTIL_SHA256 = (
    "06AF2F59CC22EB17213F67D243BAFC0FB2E4BB6627026787EDE9D4CF337387EA"
)
EXPECTED_CHESS_INIT_SHA256 = (
    "28BB8423AE3D64752713CB7430821D5B0E7CE3DC9872CA329EC3EC39FAD8EE5E"
)
EXPECTED_CHESS_UCI_SHA256 = (
    "B9E5AAD44EB2047698866AB3E141B22B6744DA6A504D96268F3A330654278991"
)
EXPECTED_CHESS_VERSION = "0.8.0"
EXPECTED_PYTHON_VERSION = (3, 12, 0)
EXPECTED_PYTHON_EXECUTABLE_SHA256 = (
    "42AC541168E97DEDB9AABD8BE335539FC41C682E414B9E8D137B164FB68683B0"
)
EXPECTED_PYTHON_RUNTIME_SHA256 = (
    "E7890E38256F04EE0B55AC5276BBF3AC61392C3A3CE150BB5497B709803E17CE"
)
EXPECTED_PSUTIL_VERSION = "7.2.2"
PSUTIL_MODULE_LABELS = {
    "psutil": "psutil_init",
    "psutil._common": "psutil_common",
    "psutil._ntuples": "psutil_ntuples",
    "psutil._psutil_windows": "psutil_native",
    "psutil._pswindows": "psutil_windows",
}
EXPECTED_PSUTIL_MODULE_SHA256 = {
    "psutil": "7B6A0675824EB1FA2FF0CB1EB36E358DC454703E51DFA4E9A0E6CCD26A159F0C",
    "psutil._common": "6FC6BF5F86491BA962521374472570238929003CEBEA8E9B6C55224084B52BB0",
    "psutil._ntuples": "96F42BC24549636B5707A949B7CC92E89C87F7CEA7D343A6D17821AC670DFCD2",
    "psutil._psutil_windows": "0035450801BD7D938E9E146C5EC28E619CB5A5F4A18CDC53AC7E9734C7F94F78",
    "psutil._pswindows": "0BBD52DCB214735BE4168D11A2AE192D5BC7265C8CF72C611179476479687F54",
}
EXPECTED_BOOK_SHA256 = (
    "28ED51C2F42E723D5E127D2D3F21C0BFA4A9B318615AFDB299B93EA62DEA2B1E"
)
EXPECTED_CONFIG_SHA256 = (
    "30A4779FDE75B5259F732A148872AA81DCA96DA7C766238D0153A591D6624E37"
)
EXPECTED_NET_SHA256 = (
    "99DC67EABF26A64FAEECA3A88B4C38597A840B8D4A874B9F2CF658C6F92A04A6"
)
CANDIDATE_NNUE_ARCHITECTURE_MARKER = (
    "(45MiB, (45056, 1024, 16, 32, 1))"
)
NORMATIVE_TIME_CONTROLS = frozenset(
    ((2000, 20), (10000, 100), (30000, 300))
)
MATCH_FAILURE_BUDGET = 1
MATCH_NO_PROGRESS_TIMEOUT_SECONDS = 900.0
WORKER_JOIN_POLL_SECONDS = 0.5
WORKER_SHUTDOWN_TIMEOUT_SECONDS = 30.0


class GateConfigurationError(ValueError):
    """Raised when a match cannot preserve the Atomic LOS gate contract."""


@dataclass(frozen=True)
class GateConfig:
    min_total_exclusive: int
    target_los_display: str


@dataclass(frozen=True)
class GateDecision:
    total: int
    displayed_los: Optional[str]
    complete_pairs: bool
    passed: bool


@dataclass(frozen=True)
class NormativeAssetSnapshot:
    fingerprints: Tuple[FileFingerprint, ...]

    def by_label(self) -> Mapping[str, FileFingerprint]:
        return {fingerprint.label: fingerprint for fingerprint in self.fingerprints}


def normative_psutil_fingerprints(
    *,
    expected_version: Optional[str] = EXPECTED_PSUTIL_VERSION,
    expected_hashes: Optional[Mapping[str, str]] = EXPECTED_PSUTIL_MODULE_SHA256,
) -> Tuple[FileFingerprint, ...]:
    """Authenticate every loaded psutil module used by process/affinity gates."""

    if expected_version is None and expected_hashes is None:
        return ()
    actual_version = str(getattr(psutil, "__version__", ""))
    if expected_version is not None and actual_version != expected_version:
        raise GateConfigurationError(
            f"unexpected psutil version {actual_version!r}; "
            f"expected {expected_version!r}"
        )
    if expected_hashes is not None and set(expected_hashes) != set(
        PSUTIL_MODULE_LABELS
    ):
        raise GateConfigurationError(
            "normative psutil hash map does not cover the exact loaded-module set"
        )

    fingerprints = []
    package_root: Optional[Path] = None
    for module_name, label in PSUTIL_MODULE_LABELS.items():
        module = sys.modules.get(module_name)
        module_file = getattr(module, "__file__", None)
        if module_file is None:
            raise GateConfigurationError(
                f"normative psutil did not load {module_name}"
            )
        module_path = Path(str(module_file)).resolve()
        current_root = module_path.parent
        if package_root is None:
            package_root = current_root
        if current_root != package_root or package_root.name != "psutil":
            raise GateConfigurationError(
                "normative psutil modules do not share one canonical package: "
                f"{module_name}={module_path}"
            )
        fingerprint = fingerprint_file(module_path, label=label)
        if expected_hashes is not None:
            require_sha256(
                fingerprint,
                expected_hashes[module_name],
                description=f"normative {module_name}",
            )
        fingerprints.append(fingerprint)
    return tuple(fingerprints)


def descendant_pids() -> set[int]:
    try:
        return {child.pid for child in psutil.Process().children(recursive=True)}
    except psutil.Error as exc:
        raise GateConfigurationError(
            f"cannot snapshot runner child processes: {exc}"
        ) from exc


def cleanup_new_engine_processes(
    match: object,
    initial_pids: set[int],
    *,
    natural_exit_timeout: float = 2.0,
) -> Tuple[int, ...]:
    """Terminate only leaked new descendants whose executable is an engine."""

    engine_paths = {
        Path(str(path)).resolve()
        for path in getattr(match, "engine_paths", ())
    }
    candidates = []
    try:
        for child in psutil.Process().children(recursive=True):
            if child.pid in initial_pids:
                continue
            try:
                if Path(child.exe()).resolve() in engine_paths:
                    candidates.append(child)
            except (psutil.Error, OSError):
                continue
    except psutil.Error as exc:
        raise GateConfigurationError(
            f"cannot inspect runner child processes: {exc}"
        ) from exc

    _, survivors = psutil.wait_procs(candidates, timeout=natural_exit_timeout)
    leaked_pids = tuple(sorted(process.pid for process in survivors))
    for process in survivors:
        try:
            process.terminate()
        except psutil.Error:
            pass
    _, survivors = psutil.wait_procs(survivors, timeout=2.0)
    for process in survivors:
        try:
            process.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(survivors, timeout=2.0)
    return leaked_pids


def parse_target_los(value: str) -> str:
    """Parse a finite percentage which is exactly representable at one decimal."""

    try:
        target = Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError("target LOS must be a number") from exc

    if not target.is_finite() or target < 0 or target > 100:
        raise argparse.ArgumentTypeError("target LOS must be between 0.0 and 100.0")

    one_decimal = target.quantize(Decimal("0.1"))
    if target != one_decimal:
        raise argparse.ArgumentTypeError(
            "target LOS must have no precision beyond the runner's one decimal"
        )
    return format(one_decimal, ".1f")


def load_runner_module(
    runner_path: Path,
    *,
    expected_sha256: str = EXPECTED_RUNNER_SHA256,
    expected_stat_util_sha256: Optional[str] = EXPECTED_STAT_UTIL_SHA256,
    expected_chess_init_sha256: Optional[str] = EXPECTED_CHESS_INIT_SHA256,
    expected_chess_uci_sha256: Optional[str] = EXPECTED_CHESS_UCI_SHA256,
    expected_python_version: Optional[Tuple[int, int, int]] = EXPECTED_PYTHON_VERSION,
    expected_python_executable_sha256: Optional[str] = (
        EXPECTED_PYTHON_EXECUTABLE_SHA256
    ),
    expected_python_runtime_sha256: Optional[str] = EXPECTED_PYTHON_RUNTIME_SHA256,
    expected_psutil_version: Optional[str] = EXPECTED_PSUTIL_VERSION,
    expected_psutil_module_sha256: Optional[Mapping[str, str]] = (
        EXPECTED_PSUTIL_MODULE_SHA256
    ),
) -> ModuleType:
    """Import only the frozen external runner, without editing it."""

    path = runner_path.expanduser().resolve()
    try:
        runner_fingerprint = fingerprint_file(path, label="runner")
        require_sha256(
            runner_fingerprint,
            expected_sha256,
            description="external variantfishtest_new1.py runner",
        )
    except CompilerPreflightError as exc:
        raise GateConfigurationError(str(exc)) from exc

    module_name = f"_atomic_variantfishtest_{abs(hash(path))}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise GateConfigurationError(f"cannot import runner: {path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    finally:
        sys.path.pop(0)

    if not hasattr(module, "EngineMatch") or not callable(
        getattr(module, "elo_stats", None)
    ):
        raise GateConfigurationError(
            "runner must expose EngineMatch and elo_stats(scores)"
        )

    source_fingerprints = [runner_fingerprint]
    try:
        if expected_python_version is not None:
            actual_version = tuple(sys.version_info[:3])
            if platform.python_implementation() != "CPython":
                raise GateConfigurationError("normative runner requires CPython")
            if actual_version != expected_python_version:
                raise GateConfigurationError(
                    "unexpected Python version: "
                    f"{actual_version}; expected {expected_python_version}"
                )
            python_executable = fingerprint_file(
                Path(sys.executable), label="python_executable"
            )
            runtime_path = Path(sys.base_prefix) / (
                f"python{sys.version_info.major}{sys.version_info.minor}.dll"
            )
            python_runtime = fingerprint_file(
                runtime_path, label="python_runtime"
            )
            if expected_python_executable_sha256 is not None:
                require_sha256(
                    python_executable,
                    expected_python_executable_sha256,
                    description="normative Python executable",
                )
            if expected_python_runtime_sha256 is not None:
                require_sha256(
                    python_runtime,
                    expected_python_runtime_sha256,
                    description="normative Python runtime",
                )
            source_fingerprints.extend((python_executable, python_runtime))

        chess_module = getattr(module, "chess", None)
        chess_uci = getattr(chess_module, "uci", None)
        if (
            expected_chess_init_sha256 is not None
            or expected_chess_uci_sha256 is not None
        ):
            if chess_module is None or chess_uci is None:
                raise GateConfigurationError(
                    "runner did not load its local chess.uci"
                )
            chess_version = str(getattr(chess_module, "__version__", ""))
            if chess_version != EXPECTED_CHESS_VERSION:
                raise GateConfigurationError(
                    f"unexpected chess version {chess_version!r}; "
                    f"expected {EXPECTED_CHESS_VERSION!r}"
                )
            expected_chess_root = path.parent / "chess"
            chess_init_path = Path(str(chess_module.__file__)).resolve()
            chess_uci_path = Path(str(chess_uci.__file__)).resolve()
            if chess_init_path != (expected_chess_root / "__init__.py").resolve():
                raise GateConfigurationError(
                    f"runner did not load local chess package: {chess_init_path}"
                )
            if chess_uci_path != (expected_chess_root / "uci.py").resolve():
                raise GateConfigurationError(
                    f"runner did not load local chess.uci: {chess_uci_path}"
                )
            chess_init_fingerprint = fingerprint_file(
                chess_init_path, label="chess_init"
            )
            chess_uci_fingerprint = fingerprint_file(
                chess_uci_path, label="chess_uci"
            )
            if expected_chess_init_sha256 is not None:
                require_sha256(
                    chess_init_fingerprint,
                    expected_chess_init_sha256,
                    description="runner chess/__init__.py",
                )
            if expected_chess_uci_sha256 is not None:
                require_sha256(
                    chess_uci_fingerprint,
                    expected_chess_uci_sha256,
                    description="runner chess/uci.py",
                )
            source_fingerprints.extend(
                (chess_init_fingerprint, chess_uci_fingerprint)
            )

        source_fingerprints.extend(
            normative_psutil_fingerprints(
                expected_version=expected_psutil_version,
                expected_hashes=expected_psutil_module_sha256,
            )
        )
    except CompilerPreflightError as exc:
        raise GateConfigurationError(str(exc)) from exc

    # The runner's sibling module defines the statistical result. Refuse a
    # same-named module from another installation and include it in TOCTOU.
    expected_stat_util = path.parent / "stat_util.py"
    stat_util = getattr(module, "stat_util", None)
    loaded_stat_util = getattr(stat_util, "__file__", None)
    if not expected_stat_util.is_file() and expected_stat_util_sha256 is not None:
        raise GateConfigurationError(
            f"runner sibling stat_util.py does not exist: {expected_stat_util}"
        )
    if expected_stat_util.is_file():
        if (
            loaded_stat_util is None
            or Path(loaded_stat_util).resolve() != expected_stat_util.resolve()
        ):
            raise GateConfigurationError(
                f"runner did not load its sibling stat_util.py: {expected_stat_util}"
            )
        try:
            source_fingerprints.append(
                fingerprint_file(expected_stat_util, label="stat_util")
            )
            if expected_stat_util_sha256 is not None:
                require_sha256(
                    source_fingerprints[-1],
                    expected_stat_util_sha256,
                    description="external runner stat_util.py",
                )
        except CompilerPreflightError as exc:
            raise GateConfigurationError(str(exc)) from exc

    try:
        verify_file_fingerprints(source_fingerprints)
    except CompilerPreflightError as exc:
        raise GateConfigurationError(str(exc)) from exc
    setattr(module, "__atomic_source_fingerprints__", tuple(source_fingerprints))
    return module


def runner_displayed_los(runner: ModuleType, scores: Sequence[int]) -> Optional[str]:
    """Return exactly the one-decimal LOS text emitted by the external runner."""

    try:
        output = runner.elo_stats(list(scores))
    except (ValueError, ZeroDivisionError, OverflowError):
        return None
    match = LOS_RE.search(output or "")
    return match.group(1) if match else None


def evaluate_gate(
    runner: ModuleType, scores: Sequence[int], config: GateConfig
) -> GateDecision:
    """Evaluate ``Total > threshold`` and exact displayed LOS equality."""

    if len(scores) != 3:
        return GateDecision(0, None, False, False)
    try:
        numeric_scores = [int(score) for score in scores]
    except (TypeError, ValueError, OverflowError):
        return GateDecision(0, None, False, False)
    if any(score < 0 for score in numeric_scores):
        return GateDecision(sum(numeric_scores), None, False, False)

    total = sum(numeric_scores)
    displayed_los = runner_displayed_los(runner, numeric_scores)
    complete_pairs = total % 2 == 0
    passed = (
        complete_pairs
        and total > config.min_total_exclusive
        and displayed_los == config.target_los_display
    )
    return GateDecision(total, displayed_los, complete_pairs, passed)


def _normalized_options(
    raw_options: object, *, engine_label: str
) -> Mapping[str, str]:
    if not isinstance(raw_options, dict):
        raise GateConfigurationError(f"{engine_label} options must be a dictionary")
    normalized = {}
    for name, value in raw_options.items():
        key = str(name).strip().casefold()
        if key in normalized:
            raise GateConfigurationError(
                f"{engine_label} has duplicate case-insensitive option {name}"
            )
        normalized[key] = str(value).strip()
    return normalized


def validate_match_configuration(match: object, config: GateConfig) -> None:
    """Reject any runner setting outside the three normative Atomic gates."""

    if config.min_total_exclusive != 100 or config.target_los_display != "100.0":
        raise GateConfigurationError(
            "normative gate requires Total > 100 and displayed LOS exactly 100.0%"
        )
    if getattr(match, "max_games", None) != 64000:
        raise GateConfigurationError("max_games must be exactly 64000")
    if bool(getattr(match, "sprt", False)):
        raise GateConfigurationError("SPRT mode is incompatible with the exact LOS gate")
    if getattr(match, "variants", None) != ["atomic"]:
        raise GateConfigurationError("variants must be exactly ['atomic']")
    time_control = (getattr(match, "time", None), getattr(match, "inc", None))
    if time_control not in NORMATIVE_TIME_CONTROLS:
        raise GateConfigurationError(
            "time/increment must be one of (2000,20), (10000,100), (30000,300)"
        )
    if getattr(match, "threads", None) != 4:
        raise GateConfigurationError("runner worker threads (-T) must be exactly 4")
    if getattr(match, "verbosity", None) != 2:
        raise GateConfigurationError("runner verbosity must be exactly 2")
    if not getattr(match, "book", None):
        raise GateConfigurationError("the Atomic opening book (-b) is required")
    if not getattr(match, "config", None):
        raise GateConfigurationError("variants.ini (-c) is required")

    engine_options = getattr(match, "engine_options", None)
    if not isinstance(engine_options, (list, tuple)) or len(engine_options) != 2:
        raise GateConfigurationError("runner must expose exactly two engine options")
    for index, raw_options in enumerate(engine_options, 1):
        options = _normalized_options(raw_options, engine_label=f"engine{index}")
        required = {"use nnue", "threads", "hash", "evalfile"}
        missing = sorted(required - set(options))
        if missing:
            raise GateConfigurationError(
                f"engine{index} is missing required options: {', '.join(missing)}"
            )
        extra = sorted(set(options) - required)
        if extra:
            raise GateConfigurationError(
                f"engine{index} has non-normative options: {', '.join(extra)}"
            )
        if options["use nnue"].casefold() != "true":
            raise GateConfigurationError(
                "both engines must set Use NNUE=true explicitly; "
                "pure is reserved for data generation"
            )
        if options["threads"] != "1":
            raise GateConfigurationError("both engine Threads options must be exactly 1")
        if options["hash"] != "512":
            raise GateConfigurationError("both engine Hash options must be exactly 512")


def _book_path(runner: ModuleType, match: object) -> Path:
    book = getattr(match, "book", None)
    if book is True:
        runner_file = Path(str(getattr(runner, "__file__", ""))).resolve()
        return runner_file.parent / "books" / "atomic.epd"
    return Path(str(book)).expanduser().resolve()


def validate_normative_assets(
    runner: ModuleType,
    match: object,
    *,
    emit: Callable[[str], None] = print,
) -> NormativeAssetSnapshot:
    """Fingerprint and validate every normative input before engines start."""

    engine_paths = getattr(match, "engine_paths", None)
    if not isinstance(engine_paths, (list, tuple)) or len(engine_paths) != 2:
        raise GateConfigurationError(
            "runner must expose exactly two resolved engine_paths"
        )
    candidate = Path(str(engine_paths[0])).expanduser().resolve()
    baseline = Path(str(engine_paths[1])).expanduser().resolve()
    if candidate == baseline:
        raise GateConfigurationError("candidate and baseline must be different files")

    engine_options = getattr(match, "engine_options")
    options = [
        _normalized_options(raw, engine_label=f"engine{index}")
        for index, raw in enumerate(engine_options, 1)
    ]
    config = Path(str(getattr(match, "config"))).expanduser().resolve()
    book = _book_path(runner, match)
    eval_files = [
        Path(option["evalfile"]).expanduser().resolve() for option in options
    ]

    # The external runner otherwise retains the original path strings. Replace
    # every consumed value with the exact canonical path that is fingerprinted,
    # so a symlink/junction alias cannot retarget execution after preflight.
    match.engine_paths = [str(candidate), str(baseline)]
    match.config = str(config)
    match.book = str(book)
    for raw_options, eval_file in zip(engine_options, eval_files):
        for name in raw_options:
            if str(name).strip().casefold() == "evalfile":
                raw_options[name] = str(eval_file)
                break

    source_fingerprints = tuple(
        getattr(runner, "__atomic_source_fingerprints__", ())
    )
    if not source_fingerprints:
        try:
            source_fingerprints = (
                fingerprint_file(Path(str(runner.__file__)), label="runner"),
            )
        except (AttributeError, CompilerPreflightError) as exc:
            raise GateConfigurationError(
                "runner source fingerprint is unavailable"
            ) from exc

    try:
        dynamic_fingerprints = fingerprint_files(
            (
                ("candidate", candidate),
                ("baseline", baseline),
                ("config", config),
                ("book", book),
                ("engine1_eval_file", eval_files[0]),
                ("engine2_eval_file", eval_files[1]),
            )
        )
        all_fingerprints = source_fingerprints + dynamic_fingerprints
        verify_file_fingerprints(all_fingerprints)
        by_label = {item.label: item for item in all_fingerprints}
        require_sha256(
            by_label["runner"], EXPECTED_RUNNER_SHA256, description="external runner"
        )
        if "stat_util" not in by_label:
            raise CompilerPreflightError(
                "external runner stat_util.py fingerprint is unavailable"
            )
        require_sha256(
            by_label["stat_util"],
            EXPECTED_STAT_UTIL_SHA256,
            description="external runner stat_util.py",
        )
        for label, expected, description in (
            (
                "python_executable",
                EXPECTED_PYTHON_EXECUTABLE_SHA256,
                "normative Python executable",
            ),
            (
                "python_runtime",
                EXPECTED_PYTHON_RUNTIME_SHA256,
                "normative Python runtime",
            ),
            (
                "chess_init",
                EXPECTED_CHESS_INIT_SHA256,
                "runner chess/__init__.py",
            ),
            (
                "chess_uci",
                EXPECTED_CHESS_UCI_SHA256,
                "runner chess/uci.py",
            ),
        ):
            if label not in by_label:
                raise CompilerPreflightError(
                    f"{description} fingerprint is unavailable"
                )
            require_sha256(by_label[label], expected, description=description)
        for module_name, label in PSUTIL_MODULE_LABELS.items():
            description = f"normative {module_name}"
            if label not in by_label:
                raise CompilerPreflightError(
                    f"{description} fingerprint is unavailable"
                )
            require_sha256(
                by_label[label],
                EXPECTED_PSUTIL_MODULE_SHA256[module_name],
                description=description,
            )
        require_sha256(
            by_label["baseline"],
            NORMATIVE_BASELINE_SHA256,
            description="frozen Fairy-Stockfish BMI2 baseline",
        )
        require_sha256(
            by_label["config"], EXPECTED_CONFIG_SHA256, description="variants.ini"
        )
        require_sha256(
            by_label["book"], EXPECTED_BOOK_SHA256, description="Atomic opening book"
        )
        require_sha256(
            by_label["engine1_eval_file"],
            EXPECTED_NET_SHA256,
            description="engine1 Legacy Atomic V1 network",
        )
        require_sha256(
            by_label["engine2_eval_file"],
            EXPECTED_NET_SHA256,
            description="engine2 Legacy Atomic V1 network",
        )
    except CompilerPreflightError as exc:
        raise GateConfigurationError(str(exc)) from exc

    time_control = (getattr(match, "time"), getattr(match, "inc"))
    emit(
        "Normative LOS assets: PASS "
        "python=CPython-3.12.0 chess=0.8.0 psutil=7.2.2 "
        f"runner_sha256={EXPECTED_RUNNER_SHA256} "
        f"stat_util_sha256={EXPECTED_STAT_UTIL_SHA256} "
        f"tc={time_control[0]}+{time_control[1]} variants=['atomic'] "
        "max_games=64000 workers=4 verbosity=2 "
        f"book_sha256={EXPECTED_BOOK_SHA256} "
        f"config_sha256={EXPECTED_CONFIG_SHA256} "
        f"net_sha256={EXPECTED_NET_SHA256} "
        f"baseline_sha256={NORMATIVE_BASELINE_SHA256}"
    )
    return NormativeAssetSnapshot(all_fingerprints)


def compiler_preflight(
    match: object, assets: NormativeAssetSnapshot
) -> CompilationSignature:
    """Require exact target/compiler parity for the snapshotted engines."""

    engine_paths = getattr(match, "engine_paths", None)
    if not isinstance(engine_paths, (list, tuple)) or len(engine_paths) != 2:
        raise GateConfigurationError(
            "runner must expose exactly two resolved engine_paths"
        )
    try:
        signature = require_matching_compilation_settings(
            Path(str(engine_paths[0])),
            Path(str(engine_paths[1])),
            expected_fingerprints=assets.by_label(),
            expected_baseline_sha256=NORMATIVE_BASELINE_SHA256,
        )
        playing_engine_smoke(match)
        return signature
    except CompilerPreflightError as exc:
        raise GateConfigurationError(str(exc)) from exc


def playing_engine_smoke(
    match: object,
    *,
    timeout: float = 60.0,
    emit: Callable[[str], None] = print,
) -> None:
    """Prove both exact engines can load NNUE and complete an Atomic search."""

    engine_paths = tuple(Path(str(path)) for path in match.engine_paths)
    engine_options = tuple(match.engine_options)
    for index, (engine, raw_options) in enumerate(
        zip(engine_paths, engine_options), 1
    ):
        options = _normalized_options(
            raw_options, engine_label=f"engine{index}"
        )
        eval_file = Path(options["evalfile"])
        commands = "\n".join(
            (
                "uci",
                f"setoption name VariantPath value {match.config}",
                "setoption name UCI_Variant value atomic",
                "setoption name Threads value 1",
                "setoption name Hash value 512",
                "setoption name Use NNUE value true",
                f"setoption name EvalFile value {eval_file}",
                "isready",
                "position startpos",
                "go nodes 1",
                "quit",
                "",
            )
        )
        try:
            completed = subprocess.run(
                [str(engine)],
                input=commands,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GateConfigurationError(
                f"engine{index} playing smoke failed to run: {exc}"
            ) from exc
        lines = completed.stdout.splitlines()
        if completed.returncode != 0:
            raise GateConfigurationError(
                f"engine{index} playing smoke exited {completed.returncode}"
            )
        for marker in ("uciok", "readyok"):
            if marker not in lines:
                raise GateConfigurationError(
                    f"engine{index} playing smoke emitted no {marker}"
                )
        if not any(line.startswith("bestmove ") for line in lines):
            raise GateConfigurationError(
                f"engine{index} playing smoke emitted no bestmove"
            )
        if any("ERROR:" in line.upper() for line in lines):
            raise GateConfigurationError(
                f"engine{index} playing smoke reported an NNUE/protocol error"
            )
        selected_net = str(eval_file)
        expected_prefix = f"NNUE evaluation using {selected_net} "
        net_lines = [
            line
            for line in lines
            if expected_prefix in line
        ]
        marker_ok = (
            any(CANDIDATE_NNUE_ARCHITECTURE_MARKER in line for line in net_lines)
            if index == 1
            else any("enabled" in line for line in net_lines)
        )
        if not marker_ok:
            raise GateConfigurationError(
                f"engine{index} did not confirm its expected NNUE marker for "
                f"{selected_net}"
            )
    emit("LOS playing preflight: PASS engines=2 mode=true atomic_searches=2")


def make_gated_match_class(runner: ModuleType, config: GateConfig) -> type:
    """Add pair-safe LOS, bounded failures, and cooperative interruption."""

    class CooperativeGateMatch(runner.EngineMatch):  # type: ignore[misc, name-defined]
        gate_observed = False

        def __init__(self) -> None:
            super().__init__()
            self.gate_abort_reason: Optional[str] = None
            self.gate_failure_count = 0
            self.gate_workers: list[threading.Thread] = []
            self.gate_worker_errors: list[str] = []
            self.gate_forced_cleanup_pids: Tuple[int, ...] = ()

        def request_gate_abort(self, reason: str) -> None:
            with self.lock:
                if self.gate_abort_reason is None:
                    self.gate_abort_reason = reason

        def play_match_instance(self):
            try:
                result = super().play_match_instance()
            except Exception:
                with self.lock:
                    self.gate_failure_count += 1
                    self.gate_abort_reason = (
                        "external runner reached the match-instance failure "
                        f"budget ({self.gate_failure_count}/{MATCH_FAILURE_BUDGET})"
                    )
                raise
            return result

        def stop(self) -> bool:
            if self.gate_abort_reason is not None:
                return True
            decision = evaluate_gate(runner, self.scores, config)
            if decision.passed:
                self.gate_observed = True
                return True
            return super().stop()

        def _guarded_worker(self) -> None:
            try:
                self.worker()
            except BaseException as exc:
                with self.lock:
                    rendered = f"{type(exc).__name__}: {exc}"
                    self.gate_worker_errors.append(rendered)
                    self.gate_abort_reason = (
                        "external runner worker failed: " + rendered
                    )

        def _force_engine_cleanup(self) -> None:
            initial_pids = getattr(self, "gate_initial_pids", None)
            if initial_pids is None:
                return
            cleaned = cleanup_new_engine_processes(
                self,
                initial_pids,
                natural_exit_timeout=0.0,
            )
            self.gate_forced_cleanup_pids = tuple(
                sorted(set(self.gate_forced_cleanup_pids + cleaned))
            )

        def _join_workers_bounded(self) -> Tuple[str, ...]:
            deadline = time.monotonic() + WORKER_SHUTDOWN_TIMEOUT_SECONDS
            for worker in self.gate_workers:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                worker.join(timeout=remaining)
            return tuple(
                worker.name for worker in self.gate_workers if worker.is_alive()
            )

        def run(self) -> None:
            self.print_settings()
            self.validate_engine_variants()
            if self.book and (len(self.variants) > 1 or not self.fens):
                self.init_book()

            self.gate_workers = [
                threading.Thread(
                    target=self._guarded_worker,
                    name=f"atomic-los-worker-{index + 1}",
                    daemon=True,
                )
                for index in range(self.threads)
            ]
            for worker in self.gate_workers:
                worker.start()
            last_total = sum(self.scores)
            last_progress = time.monotonic()
            try:
                while any(worker.is_alive() for worker in self.gate_workers):
                    for worker in self.gate_workers:
                        worker.join(timeout=WORKER_JOIN_POLL_SECONDS)
                    total = sum(self.scores)
                    if total != last_total:
                        last_total = total
                        last_progress = time.monotonic()
                    if self.gate_abort_reason is not None:
                        self._force_engine_cleanup()
                        alive = self._join_workers_bounded()
                        if alive:
                            self.gate_abort_reason += (
                                "; workers did not stop: " + ", ".join(alive)
                            )
                        break
                    idle_seconds = time.monotonic() - last_progress
                    if idle_seconds >= MATCH_NO_PROGRESS_TIMEOUT_SECONDS:
                        self.request_gate_abort(
                            "external runner made no score progress for "
                            f"{idle_seconds:.1f}s"
                        )
                        self._force_engine_cleanup()
                        alive = self._join_workers_bounded()
                        if alive:
                            self.gate_abort_reason += (
                                "; workers did not stop: " + ", ".join(alive)
                            )
                        break
            except BaseException:
                self.request_gate_abort("match interrupted")
                self._force_engine_cleanup()
                self._join_workers_bounded()
                raise

            self.print_results()

    CooperativeGateMatch.__name__ = "CooperativeGateMatch"
    return CooperativeGateMatch


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Import the frozen variantfishtest_new1.py and stop cooperatively "
            "after complete pairs when its displayed LOS gate passes. Runner "
            "arguments follow --."
        )
    )
    parser.add_argument(
        "--runner", required=True, type=Path, help="path to variantfishtest_new1.py"
    )
    parser.add_argument(
        "--min-total-exclusive",
        required=True,
        type=int,
        help="normative value is 100",
    )
    parser.add_argument(
        "--target-los",
        required=True,
        type=parse_target_los,
        help="normative value is the runner's displayed 100.0 percent",
    )
    return parser


def _split_wrapper_and_runner_args(argv: Sequence[str]) -> Tuple[list[str], list[str]]:
    try:
        separator = list(argv).index("--")
    except ValueError as exc:
        raise GateConfigurationError(
            "separate wrapper options from variantfishtest arguments with --"
        ) from exc
    wrapper_args = list(argv[:separator])
    runner_args = list(argv[separator + 1 :])
    if not runner_args:
        raise GateConfigurationError("variantfishtest arguments are required after --")
    return wrapper_args, runner_args


CompilerPreflight = Callable[
    [object, NormativeAssetSnapshot], CompilationSignature
]
AssetPreflight = Callable[[ModuleType, object], NormativeAssetSnapshot]


def run_gate(
    runner: ModuleType,
    runner_args: Sequence[str],
    config: GateConfig,
    *,
    preflight: CompilerPreflight = compiler_preflight,
    asset_preflight: AssetPreflight = validate_normative_assets,
) -> Tuple[int, GateDecision]:
    """Run one gated match and verify the same files again after it ends."""

    match_class = make_gated_match_class(runner, config)
    original_argv = sys.argv
    sys.argv = [str(getattr(runner, "__file__", "variantfishtest_new1.py"))] + list(
        runner_args
    )
    match = None
    assets = None
    initial_pids = None
    try:
        try:
            match = match_class()
        except SystemExit as exc:
            raise GateConfigurationError(
                "external runner exited during match configuration "
                f"with code {exc.code!r}"
            ) from exc
    finally:
        sys.argv = original_argv

    try:
        validate_match_configuration(match, config)
        assets = asset_preflight(runner, match)
        preflight(match, assets)
        initial_pids = descendant_pids()
        match.gate_initial_pids = initial_pids
        try:
            match.run()
        except SystemExit as exc:
            raise GateConfigurationError(
                f"external runner exited during match execution with code {exc.code!r}"
            ) from exc
        abort_reason = getattr(match, "gate_abort_reason", None)
        if abort_reason is not None:
            forced_cleanup = getattr(match, "gate_forced_cleanup_pids", ())
            suffix = (
                "; terminated engine pids "
                + ", ".join(str(pid) for pid in forced_cleanup)
                if forced_cleanup
                else ""
            )
            raise GateConfigurationError(str(abort_reason) + suffix)
        decision = evaluate_gate(runner, match.scores, config)
    finally:
        try:
            if match is not None:
                match.close()
        finally:
            try:
                if match is not None and initial_pids is not None:
                    leaked_pids = cleanup_new_engine_processes(
                        match, initial_pids
                    )
                    if leaked_pids:
                        raise GateConfigurationError(
                            "runner leaked engine processes after shutdown: "
                            + ", ".join(str(pid) for pid in leaked_pids)
                        )
                    print("LOS engine-process postflight: PASS leaked=0")
            finally:
                # A runner shutdown failure must not skip immutability checks.
                # If both fail, changed input is the stronger gate violation.
                if assets is not None:
                    try:
                        verify_file_fingerprints(
                            assets.fingerprints,
                            emit=print,
                            pass_label="LOS artifact postflight",
                        )
                    except CompilerPreflightError as exc:
                        raise GateConfigurationError(str(exc)) from exc

    return (0 if decision.passed else 1), decision


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        wrapper_args, runner_args = _split_wrapper_and_runner_args(args)
        namespace = _build_parser().parse_args(wrapper_args)
        config = GateConfig(
            min_total_exclusive=namespace.min_total_exclusive,
            target_los_display=namespace.target_los,
        )
        runner = load_runner_module(namespace.runner)
        code, decision = run_gate(runner, runner_args, config)
    except GateConfigurationError as exc:
        print(f"Atomic LOS gate configuration error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Atomic LOS gate interrupted", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Atomic LOS gate runner error: {exc}", file=sys.stderr)
        return 2

    los = decision.displayed_los if decision.displayed_los is not None else "unavailable"
    status = "PASS" if decision.passed else "FAIL"
    print(
        f"Atomic LOS gate: {status} Total: {decision.total} "
        f"LOS: {los}% complete_pairs: {decision.complete_pairs}"
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
